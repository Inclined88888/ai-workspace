import importlib

import pytest
from fastapi.testclient import TestClient

KEY = "k" * 32


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("BROWSER_API_KEY", KEY)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from browser_agent import server

    importlib.reload(server)
    with TestClient(server.app) as c:
        yield c, server


def test_auth_required(client):
    c, _ = client
    assert c.get("/healthz").status_code == 200
    assert c.get("/api/jobs").status_code == 401
    assert c.get("/tools/openapi.json").status_code == 401
    assert c.get("/api/jobs", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_tool_spec_and_providers(client):
    c, _ = client
    h = {"Authorization": f"Bearer {KEY}"}
    spec = c.get("/tools/openapi.json", headers=h).json()
    ops = {o["operationId"] for p in spec["paths"].values() for o in p.values()}
    assert ops == {"browse_web", "read_page"}
    assert "providers" in c.get("/api/providers", headers=h).json()


def test_private_urls_blocked(client):
    c, _ = client
    h = {"Authorization": f"Bearer {KEY}"}
    for url in ("http://127.0.0.1:8000", "http://localhost", "file:///etc/passwd", "http://10.0.0.1"):
        assert c.get("/tools/read", params={"url": url}, headers=h).status_code == 400


def test_job_lifecycle_with_stubbed_agent(client, monkeypatch):
    c, server = client
    h = {"Authorization": f"Bearer {KEY}"}
    from browser_agent.agent import StepEvent, TaskResult

    async def fake_run_task(goal, on_step=None, **kw):
        on_step(StepEvent(1, "https://example.com", "Example", "", "", "open site", [{"navigate": {"url": "x"}}]))
        return TaskResult(goal, "ollama", "m", True, True, "three roles found", 1, ["https://example.com"])

    monkeypatch.setattr(server, "run_task", fake_run_task)
    job = c.post("/api/jobs", json={"goal": "find roles"}, headers=h).json()
    for _ in range(50):
        got = c.get(f"/api/jobs/{job['id']}", headers=h).json()
        if got["status"] == "done":
            break
        import time; time.sleep(0.05)
    assert got["status"] == "done"
    assert got["result"]["final"] == "three roles found"
    assert got["steps"][0]["next_goal"] == "open site"
    assert c.post("/api/jobs", json={"goal": "x" * 5, "provider": "bogus"}, headers=h).status_code == 400


def test_monthly_step_budget(client, monkeypatch):
    c, server = client
    h = {"Authorization": f"Bearer {KEY}"}
    monkeypatch.setattr(server, "MONTHLY_STEP_LIMIT", 30)

    async def slow(goal, **kw):
        import asyncio
        await asyncio.sleep(5)

    monkeypatch.setattr(server, "run_task", slow)
    assert c.post("/api/jobs", json={"goal": "first job", "max_steps": 25}, headers=h).status_code == 200
    r = c.post("/api/jobs", json={"goal": "second job", "max_steps": 10}, headers=h)
    assert r.status_code == 429 and "budget" in r.json()["detail"]
    assert c.get("/api/providers", headers=h).json()["usage"] == {"used": 25, "limit": 30}


def test_in_the_stack_jobs_must_use_workbench(client, monkeypatch):
    c, _ = client
    h = {"Authorization": f"Bearer {KEY}"}
    monkeypatch.setenv("BROWSER_REQUIRE_WORKBENCH", "true")
    monkeypatch.setenv("WORKBENCH_URL", "http://workbench:8000")
    r = c.post("/api/jobs", json={"goal": "find the weather", "provider": "anthropic"}, headers=h)
    assert r.status_code == 400 and "R1" in r.json()["detail"]
    providers = c.get("/api/providers", headers=h).json()
    assert [p["name"] for p in providers["providers"]] == ["workbench"] and providers["auto"] == "workbench"
