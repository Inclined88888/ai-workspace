"""Your plan: account sign-in against ai-workspace-cloud, offline-verified signed entitlements, and the Pro gate.
The cloud is faked with httpx.MockTransport and a real Ed25519 key, so the token checks are the real ones."""

import base64
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

CLOUD = "https://cloud.test"
KEY = Ed25519PrivateKey.generate()
PUB = base64.b64encode(KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
PRO = ["app", "byo_keys", "ollama", "basic_themes", "hosted_models", "computer_use", "site_watchers", "learning", "phone_sync", "premium_themes"]


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(payload: dict, key=KEY) -> str:
    body = _b64(json.dumps(payload).encode())
    return f"aiw1.{body}.{_b64(key.sign(f'aiw1.{body}'.encode()))}"


class FakeCloud:
    def __init__(self):
        self.plan, self.live, self.down, self.calls = "free", True, False, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        if self.down:
            raise httpx.ConnectError("offline")
        body = json.loads(req.content or b"{}")
        p = req.url.path
        if p == "/v1/plans":
            return httpx.Response(200, json={"plans": {}, "billing_live": self.live})
        if p == "/v1/auth/email/start":
            return httpx.Response(200, json={"login_id": "L1", "poll_token": "P1", "expires_in": 900})
        if p == "/v1/auth/email/code":
            if body["code"] != "123456":
                return httpx.Response(400, json={"detail": "That code is not right"})
            return httpx.Response(200, json={"status": "approved", "session_token": "sess-abc", "user": {"email": "amy@example.com"}})
        auth = req.headers.get("authorization")
        if auth != "Bearer sess-abc":
            return httpx.Response(401, json={"detail": "Sign in again"})
        if p == "/v1/entitlement":
            feats = PRO if self.plan == "pro" else PRO[:4]
            t = int(time.time())
            tok = sign({"v": 1, "kid": "k", "sub": "u1", "email": "amy@example.com", "device": body["device_id"], "plan": self.plan,
                        "features": feats, "team": None, "iat": t, "exp": t + 86400, "grace": 7 * 86400})
            return httpx.Response(200, json={"token": tok, "public_key": PUB, "entitlement": {
                "plan_name": self.plan.title(), "credits": 5000 if self.plan == "pro" else 0, "status": "active", "renews_at": None,
                "team": None, "source": "personal"}})
        if p == "/v1/billing/checkout":
            return httpx.Response(200, json={"url": "https://checkout.stripe.test/c/1"})
        if p == "/v1/auth/logout":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": "nope"})


@pytest.fixture
def cloud(client, monkeypatch):
    from app import account, db

    fake = FakeCloud()
    monkeypatch.setenv("AIW_CLOUD_URL", CLOUD)
    monkeypatch.setenv("AIW_CLOUD_PUBLIC_KEY", PUB)
    monkeypatch.delenv("AIW_ENFORCE_PLANS", raising=False)
    monkeypatch.setattr(account, "_http", httpx.Client(transport=httpx.MockTransport(fake)))
    yield fake
    for k in ("account_session", "account_token", "account_email", "account_login", "account_billing_live",
              "account_refreshed_at", "account_plans_checked_at", "account_summary", "account_setup_seen", "account_public_key"):
        db.set_setting(k, "")
    account._sync_cloud_connection(False)


def test_without_a_cloud_everything_is_unlocked(client, monkeypatch):
    monkeypatch.delenv("AIW_CLOUD_URL", raising=False)
    s = client.get("/api/account").json()
    assert s["cloud_configured"] is False and s["billing_live"] is False and s["plan"] == "free"
    assert all(f["unlocked"] for f in s["pro_features"]) and s["setup_prompt"] is False
    assert client.post("/api/mobile/pair/start").status_code == 200  # beta: Pro features work
    r = client.post("/api/account/sign-in/email", json={"email": "a@b.co"})
    assert r.status_code == 503 and "beta" in r.json()["detail"]


def test_free_is_gated_once_billing_is_live_and_pro_unlocks(client, cloud):
    from app import db

    s = client.get("/api/account").json()
    assert s["billing_live"] and s["setup_prompt"] and not s["signed_in"]
    r = client.post("/api/mobile/pair/start")
    assert r.status_code == 402 and r.json()["detail"]["error"] == "upgrade_required" and r.json()["detail"]["feature"] == "phone_sync"
    assert client.post("/api/desk/jobs", json={}).status_code == 402
    assert client.post("/api/sites/watches/3/run").status_code == 402
    # never gated: reading, stopping, approving, deleting your own data, and every Free feature
    assert client.post("/api/desk/stop").status_code != 402
    assert client.post("/api/desk/jobs/j1/cancel").status_code != 402
    assert client.delete("/api/sites/watches/3").status_code != 402
    assert client.get("/api/providers").status_code == 200

    assert client.post("/api/account/sign-in/email", json={"email": "amy@example.com"}).json()["sent"]
    bad = client.post("/api/account/sign-in/code", json={"code": "000000"})
    assert bad.status_code == 400 and "not right" in bad.json()["detail"]
    s = client.post("/api/account/sign-in/code", json={"code": "123456"}).json()
    assert s["signed_in"] and s["email"] == "amy@example.com" and s["plan"] == "free"
    assert client.post("/api/mobile/pair/start").status_code == 402  # signed in, still Free
    assert "sess-abc" not in json.dumps(s) and "sess-abc" not in (db.get_setting("account_session") or "")  # encrypted at rest
    assert client.post("/api/account/checkout", json={"product": "pro_monthly"}).json()["url"].startswith("https://checkout")

    cloud.plan = "pro"
    s = client.post("/api/account/refresh").json()
    assert s["plan"] == "pro" and s["credits"] == 5000 and all(f["unlocked"] for f in s["pro_features"])
    assert client.post("/api/mobile/pair/start").status_code == 200
    conn = db.one("SELECT * FROM connections WHERE name = 'AI Workspace Cloud'")
    assert conn and conn["enabled"] == 1 and conn["base_url"] == f"{CLOUD}/v1"

    client.post("/api/account/sign-out")
    assert db.one("SELECT enabled FROM connections WHERE name = 'AI Workspace Cloud'")["enabled"] == 0  # kept, switched off
    assert client.post("/api/mobile/pair/start").status_code == 402


def test_offline_grace_and_tamper_checks(client, cloud):
    from app import account

    dev = account.device_id()
    t = int(time.time())
    p = {"v": 1, "device": dev, "plan": "pro", "features": PRO, "exp": t - 3600, "grace": 7 * 86400}
    assert account.verify(sign(p), [PUB])["plan"] == "pro"  # expired an hour ago, inside the offline grace
    assert account.verify(sign({**p, "exp": t - 8 * 86400}), [PUB]) is None  # grace used up
    assert account.verify(sign({**p, "device": "someone-else"}), [PUB]) is None  # copied from another computer
    assert account.verify(sign(p, Ed25519PrivateKey.generate()), [PUB]) is None  # not signed by the cloud
    body = _b64(json.dumps({**p, "plan": "business"}).encode())
    assert account.verify(f"aiw1.{body}.{sign(p).split('.')[2]}", [PUB]) is None  # edited payload
    assert account.verify("garbage", [PUB]) is None


def test_network_failure_keeps_pro_and_billing_stays_sticky(client, cloud):
    from app import db

    cloud.plan = "pro"
    client.post("/api/account/sign-in/email", json={"email": "amy@example.com"})
    client.post("/api/account/sign-in/code", json={"code": "123456"})
    cloud.down = True
    s = client.post("/api/account/refresh").json()
    assert s["plan"] == "pro" and s["billing_live"]  # offline: the signed token carries on
    db.set_setting("account_token", "")
    assert client.get("/api/account").json()["billing_live"]  # blocking the server does not switch enforcement off
    assert client.post("/api/mobile/pair/start").status_code == 402


def test_skip_setup_hides_the_prompt(client, cloud):
    assert client.get("/api/account").json()["setup_prompt"]
    client.post("/api/account/skip")
    assert not client.get("/api/account").json()["setup_prompt"]
