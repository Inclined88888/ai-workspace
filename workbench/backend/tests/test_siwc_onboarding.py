"""First run ("Choose how to think"): key detection and mocked validation, the honest subscription list, and Sign in
with ChatGPT end to end against a fake OpenAI auth server (RS256 ID token, PKCE, registration, refresh, sign-out)."""

import base64
import hashlib
import json
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
import uvicorn
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from conftest import OAUTH_TOKEN, _port

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
auth = FastAPI()
TOKEN_CALLS: list[dict] = []
REVOKED: list[str] = []
STATE = {"nonce": "", "grant_plan": True, "issuer": ""}


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def id_token(claims: dict) -> str:
    h = b64(json.dumps({"alg": "RS256", "kid": "k1", "typ": "JWT"}).encode())
    p = b64(json.dumps(claims).encode())
    sig = KEY.sign(f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{h}.{p}.{b64(sig)}"


@auth.get("/.well-known/openid-configuration")
def disco():
    return {"issuer": STATE["issuer"], "jwks_uri": STATE["issuer"] + "/jwks", "revocation_endpoint": STATE["issuer"] + "/oauth/revoke"}


@auth.get("/jwks")
def jwks():
    n = KEY.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "kid": "k1", "alg": "RS256", "n": b64(n.n.to_bytes(256, "big")), "e": b64(n.e.to_bytes(3, "big"))}]}


@auth.post("/api/accounts/oauth/token")
async def token(req: Request):
    form = dict((await req.form()).items())
    TOKEN_CALLS.append(form)
    if form["grant_type"] == "authorization_code":
        if form["code"] != "good-code" or not form.get("code_verifier") or form["client_id"] != "oaiapp_test":
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        scope = "openid profile email offline_access resource.invoke" + (" chatgpt.tokens.use.direct" if STATE["grant_plan"] else "")
        claims = {"iss": STATE["issuer"], "aud": "oaiapp_test", "sub": "user-1", "email": "you@example.com",
                  "exp": time.time() + 3600, "nonce": STATE["nonce"]}
        return {"access_token": OAUTH_TOKEN, "refresh_token": "r1", "id_token": id_token(claims), "expires_in": 3600, "scope": scope}
    if form["grant_type"] == "refresh_token" and form["refresh_token"] == "r1":
        return {"access_token": OAUTH_TOKEN, "refresh_token": "r2", "expires_in": 3600}
    return JSONResponse({"error": "invalid_grant"}, status_code=400)


@auth.post("/oauth/revoke")
async def revoke(req: Request):
    REVOKED.append(dict((await req.form()).items())["token"])
    return {}


@pytest.fixture(scope="module")
def auth_url():
    import socket

    port = _port()
    server = uvicorn.Server(uvicorn.Config(auth, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    STATE["issuer"] = f"http://127.0.0.1:{port}"
    yield STATE["issuer"]
    server.should_exit = True


@pytest.fixture
def siwc_env(client, auth_url, fake_url, monkeypatch):
    monkeypatch.setenv("SIWC_AUTH_BASE", auth_url)
    monkeypatch.setenv("SIWC_API_BASE", fake_url)
    monkeypatch.setenv("SIWC_PORT", str(_port()))
    TOKEN_CALLS.clear()
    REVOKED.clear()
    STATE["grant_plan"] = True
    yield
    from app import db

    for c in db.all("SELECT id FROM connections WHERE name LIKE 'ChatGPT%'"):
        client.delete(f"/api/providers/{c['id']}")


def _begin(client) -> dict:
    from app import siwc

    start = client.post("/api/auth/chatgpt/start", json={}).json()
    q = {k: v[0] for k, v in parse_qs(urlparse(start["url"]).query).items()}
    STATE["nonce"] = siwc._attempts[q["state"]]["nonce"]
    return {"start": start, "q": q}


def test_key_detection_needs_no_network(client):
    for key, preset in [("AIzaSyD-abcdefghijklmnop", "gemini"), ("gsk_abcdefghijklmnop", "groq"), ("sk-or-v1-abcdef1234", "openrouter"),
                        ("sk-ant-api03-abcdef", "anthropic"), ("xai-abcdefghijkl", "xai"), ("sk-proj-abcdefgh", "openai"),
                        ("not-a-known-key", None)]:
        assert client.post("/api/providers/detect", json={"api_key": key}).json()["preset"] == preset, key


def test_validation_checks_the_key_before_saving(client, fake_url):
    from app import db

    before = db.one("SELECT COUNT(*) AS n FROM connections")["n"]
    bad = client.post("/api/providers/validate", json={"api_key": "gsk_wrongwrongwrong", "base_url": fake_url.replace("/v1", "/leaky/v1"),
                                                       "save": True}).json()
    assert bad["preset"] == "groq" and not bad["ok"] and "rejected" in bad["detail"]
    assert "LEAKED" not in json.dumps(bad)  # the provider's error text (which quotes keys) never comes back
    assert db.one("SELECT COUNT(*) AS n FROM connections")["n"] == before  # nothing saved
    good = client.post("/api/providers/validate", json={"api_key": "gsk_rightrightright", "base_url": fake_url, "save": True}).json()
    assert good["ok"] and good["provider"]["name"] == "Groq" and good["provider"]["key_hint"] == "…ight"
    again = client.post("/api/providers/validate", json={"api_key": "gsk_rightrightright", "base_url": fake_url, "save": True}).json()
    assert again["provider"]["id"] == good["provider"]["id"]  # pasting the same provider twice updates, not duplicates
    client.delete(f"/api/providers/{good['provider']['id']}")


def test_only_permitted_subscription_sign_ins_are_offered(client):
    opts = {o["id"]: o for o in client.get("/api/auth/subscriptions").json()["options"]}
    assert opts["chatgpt"]["available"]
    for blocked in ("grok", "gemini"):
        assert not opts[blocked]["available"] and opts[blocked]["source"].startswith("https://")
    # Claude is never an in-app sign-in (Anthropic's terms): it is offered only as a hand-off to the user's own Claude Code.
    claude = opts["claude"]
    assert claude["via"] == "claude_code" and claude["source"].startswith("https://code.claude.com/")
    assert "never passes through this app" in claude["detail"]


def test_onboarding_free_cloud_then_auto_is_the_default(client, fake_url):
    """What the first-run screen does for "Free cloud": validate+save a pasted key, then make the router the default."""
    from app import db

    saved = db.get_setting("default_model")
    r = client.post("/api/providers/validate", json={"api_key": "AIzaSyFAKEFAKEFAKEFAKE", "base_url": fake_url, "save": True}).json()
    try:
        assert r["ok"] and r["preset"] == "gemini"
        assert client.put("/api/settings", json={"default_model": "auto"}).json()["default_model"] == "auto"
        assert client.put("/api/settings", json={"default_model": "nope"}).status_code == 400
    finally:
        client.delete(f"/api/providers/{r['provider']['id']}")
        db.set_setting("default_model", saved or "")


def test_sign_in_with_chatgpt_end_to_end(client, siwc_env):
    from app import db, llm, siwc
    from app.security import decrypt

    b = _begin(client)
    q = b["q"]
    # First sign-in registers this app: OpenAI's open-source client registration, never another vendor's client id
    assert q["client_id"] == "dynamic_agent_client" and q["agent_name_hint"] == "AI Workspace" and q["ext_agent_host_id"]
    assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/auth/callback")
    assert q["code_challenge_method"] == "S256" and "chatgpt.tokens.use.direct" in q["scope"]
    assert q["resource"] == "https://api.openai.com/v1"
    # The loopback listener got the redirect (simulated with the same URL a browser would open)
    import httpx
    r = httpx.get(f"{q['redirect_uri']}?code=good-code&state={q['state']}&client_id=oaiapp_test", timeout=10)
    assert r.status_code == 200 and "signed in" in r.text.lower()
    st = client.get(f"/api/auth/chatgpt/status/{q['state']}").json()
    assert st["status"] == "connected" and st["email"] == "you@example.com"
    conn = db.one("SELECT * FROM connections WHERE id = ?", st["connection_id"])
    assert conn["auth_kind"] == "oauth_subscription" and conn["name"] == "ChatGPT (you@example.com)"
    rec = json.loads(decrypt(conn["secret_enc"]))
    assert rec["client_id"] == "oaiapp_test" and rec["refresh_token"] == "r1"
    ex = TOKEN_CALLS[-1]
    assert hashlib.sha256(ex["code_verifier"].encode()).digest() == base64.urlsafe_b64decode(q["code_challenge"] + "==")
    # Uses the plan: Responses with store:false, stream:true (OpenAI's requirement), and the router puts it first
    res = client.post("/api/assistant/chat", json={"message": "hello", "model": f"{conn['id']}/gpt-plan"})
    assert res.status_code == 200
    from conftest import NATIVE_CALLS, events
    assert not [e for e in events(res) if e["type"] == "error"], events(res)
    body = NATIVE_CALLS[-1]["body"]
    assert body["store"] is False and body["stream"] is True
    from app import router
    assert router.ordered_connections()[0]["id"] == conn["id"]
    # Refresh when the access token is near expiry
    rec["expires_at"] = time.time() - 5
    from app.security import store_connection_secret
    store_connection_secret(conn["id"], json.dumps(rec))
    from app.adapters import credential
    assert credential(db.one("SELECT * FROM connections WHERE id = ?", conn["id"])) == OAUTH_TOKEN
    assert TOKEN_CALLS[-1]["grant_type"] == "refresh_token" and TOKEN_CALLS[-1]["client_id"] == "oaiapp_test"
    # The next sign-in reuses the issued registration
    b2 = client.post("/api/auth/chatgpt/start", json={"account": "user-1"}).json()
    assert "client_id=oaiapp_test" in b2["url"] and "dynamic_agent_client" not in b2["url"]
    # Sign out revokes first, then forgets the tokens
    out = client.post(f"/api/auth/chatgpt/signout/{conn['id']}").json()
    assert REVOKED == ["r2"] and out
    assert db.one("SELECT state FROM connections WHERE id = ?", conn["id"])["state"] == "needs_auth"
    llm._model_cache.clear()


def test_chatgpt_sign_in_refused_without_the_plan_scope_or_with_a_bad_state(client, siwc_env):
    b = _begin(client)
    STATE["grant_plan"] = False
    res = client.post("/api/auth/chatgpt/complete", json={"url": f"{b['q']['redirect_uri']}?code=good-code&state={b['q']['state']}&client_id=oaiapp_test"}).json()
    assert res["status"] == "failed" and "wasn't allowed" in res["error"]
    forged = client.post("/api/auth/chatgpt/complete", json={"url": "http://127.0.0.1:1455/auth/callback?code=x&state=forged"}).json()
    assert forged["status"] == "failed"
    b = _begin(client)
    denied = client.post("/api/auth/chatgpt/complete", json={"url": f"{b['q']['redirect_uri']}?error=access_denied&state={b['q']['state']}"}).json()
    assert "didn't allow" in denied["error"]
