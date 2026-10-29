"""Item 25: every integration has a health status, checked on request, failures shown as status (R4), redacted (R7)."""

import asyncio


def _by_name(report: dict) -> dict:
    return {c["name"]: c for c in report["checks"]}


def test_status_page_reports_every_part_and_the_worst_wins(client, fake_url):
    from app import db

    r = client.get("/api/health", params={"fresh": "true"}).json()
    checks = _by_name(r)
    assert checks["Database"]["status"] == "ok" and f"schema v{db.SCHEMA_VERSION}" in checks["Database"]["detail"]
    assert {f"{n} loop" for n in ("Calendar sync", "Scheduler", "Telegram", "Company")} <= set(checks)
    assert all(checks[f"{n} loop"]["status"] == "ok" for n in ("Scheduler", "Company"))
    assert checks["Fake"]["status"] == "ok" and checks["Fake"]["detail"] == "1 model" and checks["Fake"]["kind"] == "model connection"
    assert checks["Browser agent"]["status"] == "off" and checks["Web search"]["status"] == "off"

    pid = client.post("/api/providers", json={"name": "Down", "kind": "openai", "base_url": "http://127.0.0.1:9/v1", "models": "m"}).json()["id"]
    try:
        r = client.get("/api/health", params={"fresh": "true"}).json()
        down = _by_name(r)["Down"]
        assert down["status"] == "down" and "unreachable" in down["detail"] and r["status"] == "down"
        assert "unreachable" in db.one("SELECT last_error FROM connections WHERE id = ?", pid)["last_error"]
        assert db.one("SELECT last_ok_at FROM connections WHERE id = ?", client.pid)["last_ok_at"]
        cached = client.get("/api/health").json()
        assert cached["checked_at"] == r["checked_at"]  # served from the one minute cache, no new probes
    finally:
        client.delete(f"/api/providers/{pid}")
    assert client.get("/api/health", params={"fresh": "true"}).json()["status"] == "ok"


def test_failures_are_statuses_and_carry_no_secrets(client, fake_url, monkeypatch):
    from app import config, health, search

    pid = client.post("/api/providers", json={"name": "Leaky", "kind": "openai", "base_url": fake_url.replace("/v1", "/leaky/v1"),
                                              "api_key": "sk-proj-LEAKEDabcdefghijklmnopqrstu"}).json()["id"]
    monkeypatch.setattr(config, "BROWSER_URL", fake_url.removesuffix("/v1"))
    monkeypatch.setattr(config, "BROWSER_API_KEY", "k" * 32)
    monkeypatch.setattr(search, "provider", lambda: "brave")

    async def boom():
        raise RuntimeError("calendar loop crashed")

    async def dead_task():
        t = asyncio.ensure_future(boom())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return t

    task = asyncio.new_event_loop().run_until_complete(dead_task())
    monkeypatch.setitem(health.LOOPS, "Calendar sync", task)
    try:
        r = client.get("/api/health", params={"fresh": "true"}).json()
        checks = _by_name(r)
        assert checks["Leaky"]["status"] == "down" and "LEAKED" not in str(r)
        assert checks["Browser agent"]["status"] == "ok"
        assert checks["Web search (brave)"]["status"] == "ok" and "not probed" in checks["Web search (brave)"]["detail"]
        assert checks["Calendar sync loop"]["status"] == "down" and "calendar loop crashed" in checks["Calendar sync loop"]["detail"]
    finally:
        client.delete(f"/api/providers/{pid}")


def test_low_disk_degrades_and_an_unreadable_database_fails_healthz(client, monkeypatch):
    import shutil

    from app import db, health

    usage = shutil.disk_usage("/")
    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: usage._replace(free=200 * 2**20))
    assert health.database()["status"] == "degraded"
    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: usage._replace(free=10 * 2**20))
    assert health.database()["status"] == "down"
    monkeypatch.undo()

    assert client.get("/healthz").json() == {"ok": True, "database": "ok"}

    def broken(*a):
        raise RuntimeError("database disk image is malformed")

    monkeypatch.setattr(db, "one", broken)
    r = client.get("/healthz")
    assert r.status_code == 503 and r.json()["database"] == "down"


def test_health_needs_sign_in(client):
    from fastapi.testclient import TestClient

    from app import main

    assert TestClient(main.app).get("/api/health").status_code == 401
