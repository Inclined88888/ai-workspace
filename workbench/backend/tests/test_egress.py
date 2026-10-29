"""Item 75, R8: every outbound connection is in the audit log, local-only mode is provable from it, and it leaks no
secrets. No test here contacts an external host: external rows are recorded directly."""

import pathlib
import re

import pytest
from conftest import SCRIPT, events


def _last_egress(**where) -> dict:
    from app import db
    cond = " AND ".join(f"{k} = ?" for k in where) or "1"
    return db.one(f"SELECT * FROM actions WHERE kind = 'egress' AND {cond} ORDER BY id DESC", *where.values())


@pytest.mark.parametrize("host,local", [
    ("localhost", True), ("127.0.0.1", True), ("::1", True), ("10.0.0.5", True), ("192.168.1.20", True), ("fe80::1", True),
    ("host.docker.internal", True), ("ollama", True), ("browser", True), ("printer.local", True),
    ("api.openai.com", False), ("api.anthropic.com", False), ("8.8.8.8", False), ("openrouter.ai", False), ("2606:4700::1111", False),
])
def test_local_and_external_hosts(host, local):
    from app import egress
    assert egress.is_local(host) is local


def test_model_calls_are_recorded_sync_and_async(client, fake_url):
    from app import db
    port = fake_url.split(":")[2].split("/")[0]
    SCRIPT[:] = [{"role": "assistant", "content": "hi"}]
    before = db.one("SELECT COALESCE(MAX(id), 0) AS m FROM actions")["m"]
    events(client.post("/api/assistant/chat", json={"message": "hello egress"}))
    row = db.one("SELECT * FROM actions WHERE kind = 'egress' AND id > ? AND args LIKE 'POST %/v1/chat/completions' ORDER BY id", before)
    assert row and row["provider"] == "127.0.0.1" and row["effect"] == "local" and row["tool"] == "adapters"
    assert row["outcome"] == "ok" and row["args"] == f"POST http://127.0.0.1:{port}/v1/chat/completions"
    client.get("/api/models", params={"fresh": "true"})  # model listing uses the sync client
    listing = _last_egress(args=f"GET http://127.0.0.1:{port}/v1/models")
    assert listing and listing["id"] > before and listing["tool"] == "adapters"


def test_failures_are_recorded_and_the_request_still_fails_normally(client):
    pid = client.post("/api/providers", json={"name": "Nowhere", "kind": "openai", "base_url": "http://127.0.0.1:9/v1", "models": "m"}).json()["id"]
    try:
        client.get("/api/health", params={"fresh": "true"})
        row = _last_egress(args="GET http://127.0.0.1:9/v1/models")
        assert row["outcome"] == "error" and row["failure_reason"] == "ConnectError" and row["tool"] == "adapters"
    finally:
        client.delete(f"/api/providers/{pid}")


def test_external_rows_break_local_only_and_carry_no_secrets(client):
    from app import db, egress

    assert client.get("/api/egress").json()["local_only"] is True  # the test session only talks to 127.0.0.1
    egress.record("POST", "https://api.telegram.org/bot123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/getUpdates?offset=5&key=zzz",
                  "ok", 3, component="telegram")
    try:
        row = _last_egress(provider="api.telegram.org")
        assert row["effect"] == "external" and "AAHdq" not in row["args"] and "/bot<redacted>/" in row["args"]
        assert "offset" not in row["args"] and "zzz" not in row["args"]  # no query strings in the log
        s = client.get("/api/egress").json()
        assert s["local_only"] is False and s["external_requests"] == 1
        host = next(h for h in s["hosts"] if h["host"] == "api.telegram.org")
        assert host["effect"] == "external" and host["components"] == "telegram" and host["requests"] == 1
        assert "AAHdq" not in str(s)
    finally:
        db.run("DELETE FROM actions WHERE id = ?", row["id"])


def test_caldav_through_requests_and_mail_through_imap_are_recorded(client, fake_url, monkeypatch):
    from fastapi import HTTPException

    from app import calendar_sync, mail

    with pytest.raises(HTTPException):
        calendar_sync.discover(fake_url.removesuffix("/v1") + "/dav/", "u", "p")
    row = _last_egress(tool="calendar_sync")
    assert row and row["args"].startswith("PROPFIND http://127.0.0.1:") and row["effect"] == "local"

    def refuse(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", refuse)
    with pytest.raises(HTTPException):
        mail.test({"imap_host": "imap.example.com", "imap_port": 993, "username": "u", "password_enc": ""})
    row = _last_egress(provider="imap.example.com")
    assert row["args"] == "LOGIN imaps://imap.example.com:993" and row["effect"] == "external"
    assert row["outcome"] == "error" and row["failure_reason"] == "OSError" and row["tool"] == "mail"
    from app import db
    db.run("DELETE FROM actions WHERE id = ?", row["id"])


def test_a_broken_audit_write_never_breaks_the_call(client, monkeypatch):
    from app import actions

    real_add = actions.add

    def add(kind, *a, **kw):
        if kind == "egress":
            raise RuntimeError("disk full")
        return real_add(kind, *a, **kw)

    monkeypatch.setattr(actions, "add", add)
    SCRIPT[:] = [{"role": "assistant", "content": "still answering"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    assert any(e.get("content") == "still answering" for e in ev)


def test_no_unaudited_network_library_in_the_app():
    """Coverage of the audit log, checked in the source: only wrapped libraries may reach the network."""
    allowed = {"httpx": None, "imaplib": {"mail.py"}, "smtplib": {"mail.py"}, "caldav": {"calendar_sync.py"},
               "socket": {"research.py"}}  # research: DNS lookups only, to refuse private addresses
    forbidden = re.compile(r"^\s*(?:import|from)\s+(requests|urllib3|urllib\.request|http\.client|aiohttp|ftplib|telnetlib|"
                           r"websockets?|paramiko|pycurl|grpc)\b", re.M)
    app = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for f in sorted(app.glob("*.py")):
        src = f.read_text()
        if f.name != "egress.py":  # the audit wrapper itself imports requests to wrap it
            offenders += [f"{f.name}: {m.group(0).strip()}" for m in forbidden.finditer(src)]
        for lib, files in allowed.items():
            if files is not None and f.name not in files and re.search(rf"^\s*(?:import|from)\s+{lib}\b", src, re.M):
                offenders.append(f"{f.name}: {lib} outside {sorted(files)}")
    research = (app / "research.py").read_text()
    assert set(re.findall(r"socket\.(\w+)", research)) <= {"getaddrinfo", "gaierror", "AF_INET", "AF_INET6"}
    assert not offenders, offenders


def test_egress_needs_sign_in(client):
    from fastapi.testclient import TestClient

    from app import main

    assert TestClient(main.app).get("/api/egress").status_code == 401
