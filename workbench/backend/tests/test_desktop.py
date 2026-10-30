"""Desktop app mode (desktop/): one-time session token, welcome flag, and provider keys in the OS keychain."""

import keyring
import pytest
from keyring.backend import KeyringBackend



class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def get_password(self, service, username):
        return self.items.get((service, username))

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


@pytest.fixture
def desktop(client, monkeypatch):
    from app import config

    kr = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(kr)
    monkeypatch.setattr(config, "DESKTOP", True)
    monkeypatch.setattr(config, "DESKTOP_TOKEN", "t" * 43)
    monkeypatch.setattr(config, "SECRET_BACKEND", "keychain")
    yield kr
    keyring.set_keyring(previous)


def test_desktop_session_is_refused_outside_the_desktop_app(client):
    r = client.get("/api/desktop/session", params={"token": "anything"}, follow_redirects=False)
    assert r.status_code == 403


def test_desktop_session_needs_the_launch_token(client, desktop):
    assert client.get("/api/desktop/session", params={"token": "wrong"}, follow_redirects=False).status_code == 403
    r = client.get("/api/desktop/session", params={"token": "t" * 43}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] in ("/", "/welcome")
    assert "wb_session" in r.headers.get("set-cookie", "")


def test_welcome_flag_is_reported_and_routes_the_session(client, desktop):
    from app import db

    db.set_setting("desktop_welcome_done", "0")
    s = client.get("/api/status").json()
    assert s["desktop"] is True and s["welcome_done"] is False
    r = client.get("/api/desktop/session", params={"token": "t" * 43}, follow_redirects=False)
    assert r.headers["location"] == "/welcome"
    assert client.post("/api/desktop/welcome-done").json() == {"ok": True}
    assert client.get("/api/status").json()["welcome_done"] is True
    r = client.get("/api/desktop/session", params={"token": "t" * 43}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_provider_keys_live_in_the_keychain_not_the_database(client, desktop, fake_url):
    from app import db

    r = client.post("/api/providers", json={"name": "Keyed", "kind": "openai", "base_url": fake_url, "api_key": "sk-test-secret-123456"})
    pid = r.json()["id"]
    stored = db.one("SELECT secret_enc FROM connections WHERE id = ?", pid)["secret_enc"]
    assert stored == f"keychain:connection:{pid}"
    assert desktop.items[("ai-workspace", f"connection:{pid}")] == "sk-test-secret-123456"
    assert client.get("/api/providers").json()["providers"][-1]["key_hint"] == "…3456"
    assert client.get("/api/desktop/info").json()["secret_backend"] == "os-keychain"

    client.put(f"/api/providers/{pid}", json={"name": "Keyed", "kind": "openai", "base_url": fake_url, "api_key": "sk-rotated-abcdef"})
    assert desktop.items[("ai-workspace", f"connection:{pid}")] == "sk-rotated-abcdef"

    client.delete(f"/api/providers/{pid}")
    assert ("ai-workspace", f"connection:{pid}") not in desktop.items


def test_docker_mode_keeps_fernet_encryption(client, fake_url):
    from app import db, keychain

    r = client.post("/api/providers", json={"name": "Plain", "kind": "openai", "base_url": fake_url, "api_key": "sk-plain-999999"})
    pid = r.json()["id"]
    stored = db.one("SELECT secret_enc FROM connections WHERE id = ?", pid)["secret_enc"]
    assert stored and not keychain.is_ref(stored) and "sk-plain" not in stored
    client.delete(f"/api/providers/{pid}")


def test_desktop_presets_point_at_this_computer(client, monkeypatch):
    import importlib

    from app import config, llm

    monkeypatch.setattr(config, "DESKTOP", True)
    monkeypatch.setattr(config, "OLLAMA_URL", "")
    try:
        importlib.reload(llm)
        assert llm.PRESETS["ollama"]["base_url"] == "http://127.0.0.1:11434"
        assert "host.docker.internal" not in llm.PRESETS["compatible"]["base_url"]
    finally:
        monkeypatch.setattr(config, "DESKTOP", False)
        importlib.reload(llm)


def test_launcher_handoff_code_is_one_time(client, desktop):
    assert client.post("/api/desktop/handoff").status_code == 403
    assert client.post("/api/desktop/handoff", headers={"Authorization": "Bearer wrong"}).status_code == 403
    code = client.post("/api/desktop/handoff", headers={"Authorization": "Bearer " + "t" * 43}).json()["code"]
    r = client.get("/api/desktop/handoff", params={"code": code}, follow_redirects=False)
    assert r.status_code == 303 and "wb_session" in r.headers.get("set-cookie", "")
    again = client.get("/api/desktop/handoff", params={"code": code}, follow_redirects=False)
    assert again.status_code == 403 and "already been used" in again.text


def test_launcher_handoff_needs_desktop_mode(client):
    assert client.post("/api/desktop/handoff", headers={"Authorization": "Bearer x"}).status_code == 403
    assert client.get("/api/desktop/handoff", params={"code": "nope"}, follow_redirects=False).status_code == 403
