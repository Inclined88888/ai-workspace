"""Sign in with ChatGPT for open-source apps: use a ChatGPT plan without an API key (item 101).

Follows OpenAI's open-source token-sharing guide (developers.openai.com/siwc/token-sharing-open-source, read 2026-10-30):

1. First sign-in on this computer registers a client: authorize with client_id=dynamic_agent_client, our app name as
   agent_name_hint and this host's stable ext_agent_host_id. The callback returns the issued client_id (oaiapp_...),
   which is saved and reused for every later sign-in to that ChatGPT account. No client secret, no partner key, and no
   other vendor's client id is ever used.
2. PKCE (S256), fresh state and nonce per attempt, an http://127.0.0.1:<port>/auth/callback loopback listener.
3. Code exchange at /api/accounts/oauth/token, ID token checked against OpenAI's JWKS (issuer, audience, expiry, nonce),
   and the chatgpt.tokens.use.direct scope must be granted, or the connection is not created.
4. Tokens go to the OS keychain (desktop) or encrypted SQLite, never to logs. Refresh near expiry with the rotating
   refresh token, serialised by a lock. Sign out revokes the refresh token at the revocation endpoint first.
5. Inference is POST /v1/responses with store:false and stream:true (adapters.Responses).

OpenAI also offers this to commercial apps only after approval; this repo is MIT-licensed open source and runs on the
user's own computer, which is the case the open-source flow is for.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastapi import HTTPException

from . import db
from .security import decrypt, store_connection_secret

log = logging.getLogger("workbench.siwc")

APP_NAME = "AI Workspace"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
REGISTER_CLIENT = "dynamic_agent_client"
DEFAULT_PORT = 1455
ATTEMPT_TTL = 600
_refresh_lock = threading.Lock()
_attempts: dict[str, dict] = {}   # state -> pending attempt (verifier, nonce, redirect, client_id, status)


def auth_base() -> str:
    return os.getenv("SIWC_AUTH_BASE", "https://auth.openai.com").rstrip("/")


def api_base() -> str:
    return os.getenv("SIWC_API_BASE", "https://api.openai.com/v1").rstrip("/")


def resource() -> str:
    return os.getenv("SIWC_RESOURCE", "https://api.openai.com/v1")


def host_id() -> str:
    """This computer's stable ext_agent_host_id (an opaque id, not a secret)."""
    hid = db.get_setting("siwc_host_id")
    if not hid:
        hid = f"urn:uuid:{uuid.uuid4()}"
        db.set_setting("siwc_host_id", hid)
    return hid


def _registrations() -> dict:
    try:
        return json.loads(db.get_setting("siwc_registrations", "{}") or "{}")
    except ValueError:
        return {}


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# The loopback listener: a tiny HTTP server on 127.0.0.1 that receives one redirect
_servers: dict[int, asyncio.base_events.Server] = {}

_DONE_PAGE = ("<!doctype html><meta charset=utf-8><title>AI Workspace</title><body style=\"font:16px/1.5 Georgia,serif;"
              "background:#F5F1E8;color:#141311;display:grid;place-items:center;height:100vh;margin:0\"><div><h1 style=\"font-weight:400\">"
              "{title}</h1><p>{body}</p></div>")


async def _serve_callback(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = (await asyncio.wait_for(reader.readline(), 10)).decode(errors="replace")
        while (await asyncio.wait_for(reader.readline(), 10)).strip():
            pass
        parts = line.split(" ")
        target = parts[1] if len(parts) > 1 else "/"
        if not target.startswith("/auth/callback"):
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        else:
            result = await complete("http://127.0.0.1" + target)
            ok = result.get("status") == "connected"
            page = _DONE_PAGE.format(title="You're signed in." if ok else "Sign-in didn't finish.",
                                     body="You can close this tab and go back to AI Workspace." if ok else result.get("error", "Try again from the app."))
            data = page.encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: "
                         + str(len(data)).encode() + b"\r\n\r\n" + data)
        await writer.drain()
    except Exception:  # noqa: BLE001 - a bad request on the loopback must never take the app down
        log.warning("Sign in with ChatGPT callback failed", exc_info=False)
    finally:
        writer.close()


async def _listen(preferred: int) -> int:
    for port in (preferred, 0):
        if port and port in _servers:
            return port
        try:
            server = await asyncio.start_server(_serve_callback, "127.0.0.1", port)
        except OSError:
            continue
        real = server.sockets[0].getsockname()[1]
        _servers[real] = server
        asyncio.get_running_loop().call_later(ATTEMPT_TTL, _close, real)
        return real
    raise HTTPException(503, "Could not open a local port for the sign-in callback")


def _close(port: int) -> None:
    s = _servers.pop(port, None)
    if s:
        s.close()


async def start(account: str | None = None, listen: bool = True) -> dict:
    """Begin a sign-in. Returns the URL to open in the system browser and the state to poll."""
    for st in [k for k, a in _attempts.items() if a["created"] < time.time() - ATTEMPT_TTL]:
        _attempts.pop(st, None)
    regs = _registrations()
    reg = regs.get(account or "") if account else None
    port = await _listen(int(os.getenv("SIWC_PORT", DEFAULT_PORT))) if listen else int(os.getenv("SIWC_PORT", DEFAULT_PORT))
    redirect = f"http://127.0.0.1:{port}/auth/callback"
    verifier = _b64url(secrets.token_bytes(48))
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    params = {"response_type": "code", "redirect_uri": redirect, "scope": SCOPES, "resource": resource(), "state": state,
              "nonce": nonce, "code_challenge_method": "S256",
              "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()), "ext_agent_host_id": host_id()}
    if reg:
        params["client_id"] = reg["client_id"]
        if reg.get("email"):
            params["login_hint"] = reg["email"]
    else:
        params.update(client_id=REGISTER_CLIENT, agent_name_hint=APP_NAME)
    _attempts[state] = {"verifier": verifier, "nonce": nonce, "redirect": redirect, "client_id": reg["client_id"] if reg else None,
                        "created": time.time(), "status": "pending", "error": "", "connection_id": None}
    # The URL holds no token (id_token_hint is deliberately not sent so nothing secret is in a URL we might log)
    return {"url": f"{auth_base()}/api/accounts/authorize?{urlencode(params)}", "state": state, "redirect_uri": redirect}


def status(state: str) -> dict:
    a = _attempts.get(state)
    if not a:
        return {"status": "unknown", "error": "This sign-in attempt expired. Start again."}
    return {"status": a["status"], "error": a["error"], "connection_id": a["connection_id"], "email": a.get("email", "")}


def _fail(a: dict, msg: str) -> dict:
    a.update(status="failed", error=msg)
    return {"status": "failed", "error": msg}


def _discovery() -> dict:
    r = httpx.get(f"{auth_base()}/.well-known/openid-configuration", timeout=10)
    r.raise_for_status()
    return r.json()


def verify_id_token(token: str, client_id: str, nonce: str) -> dict:
    """RS256 signature against the provider's JWKS, then issuer, audience, expiry and nonce."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    try:
        h64, p64, s64 = token.split(".")
        header, claims = json.loads(_b64d(h64)), json.loads(_b64d(p64))
    except ValueError as e:
        raise ValueError("The ID token is malformed") from e
    if header.get("alg") != "RS256":
        raise ValueError("Unexpected ID token algorithm")
    disc = _discovery()
    keys = httpx.get(disc["jwks_uri"], timeout=10).json().get("keys", [])
    key = next((k for k in keys if k.get("kid") == header.get("kid")), None) or (keys[0] if len(keys) == 1 else None)
    if not key:
        raise ValueError("No matching signing key for the ID token")
    pub = rsa.RSAPublicNumbers(int.from_bytes(_b64d(key["e"]), "big"), int.from_bytes(_b64d(key["n"]), "big")).public_key()
    try:
        pub.verify(_b64d(s64), f"{h64}.{p64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as e:
        raise ValueError("The ID token signature is not valid") from e
    aud = claims.get("aud")
    if claims.get("iss") != disc.get("issuer", auth_base()):
        raise ValueError("The ID token has the wrong issuer")
    if client_id not in (aud if isinstance(aud, list) else [aud]):
        raise ValueError("The ID token is for another client")
    if (claims.get("exp") or 0) < time.time() - 60:
        raise ValueError("The ID token has expired")
    if claims.get("nonce") != nonce:
        raise ValueError("The ID token nonce does not match this sign-in")
    return claims


async def complete(callback_url: str) -> dict:
    """Finish an attempt from the loopback redirect, or from a callback URL the user pasted (Docker installs)."""
    q = {k: v[0] for k, v in parse_qs(urlparse(callback_url).query).items()}
    a = _attempts.get(q.get("state", ""))
    if not a:
        return {"status": "failed", "error": "This sign-in link doesn't match a sign-in started here. Start again."}
    if a["status"] != "pending":
        return status(q["state"])
    if q.get("error"):
        if q["error"] == "access_denied":
            return _fail(a, "You didn't allow AI Workspace to use your ChatGPT plan. Nothing was saved.")
        return _fail(a, f"ChatGPT sign-in failed: {q.get('error_description') or q['error']}")
    client_id = a["client_id"]
    issued = q.get("client_id")
    if client_id and issued and issued != client_id:
        return _fail(a, "ChatGPT returned a different app registration than the one selected. Nothing was changed.")
    client_id = client_id or issued
    if not client_id or client_id == REGISTER_CLIENT:
        return _fail(a, "ChatGPT didn't finish registering this app. Try again.")
    if not q.get("code"):
        return _fail(a, "The sign-in reply had no authorization code.")
    form = {"grant_type": "authorization_code", "client_id": client_id, "code": q["code"], "code_verifier": a["verifier"],
            "redirect_uri": a["redirect"], "resource": resource()}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{auth_base()}/api/accounts/oauth/token", data=form)
    except httpx.HTTPError:
        return _fail(a, "Couldn't reach OpenAI to finish signing in. Check your connection and try again.")
    if r.status_code >= 400:
        return _fail(a, f"OpenAI refused the sign-in (HTTP {r.status_code}). Try again.")
    tok = r.json()
    scopes = (tok.get("scope") or q.get("scope") or "").replace("+", " ").split()
    try:
        claims = await asyncio.to_thread(verify_id_token, tok.get("id_token", ""), client_id, a["nonce"])
    except (ValueError, httpx.HTTPError, KeyError) as e:
        return _fail(a, f"Couldn't verify the ChatGPT sign-in: {e}")
    if PLAN_SCOPE not in scopes or not tok.get("access_token"):
        return _fail(a, "You signed in, but using your ChatGPT plan wasn't allowed, so there's nothing to connect. "
                        "You can turn it on next time you sign in.")
    email = claims.get("email", "")
    record = {"email": email, "issuer": claims.get("iss"), "subject": claims.get("sub"), "client_id": client_id,
              "ext_agent_host_id": host_id(), "id_token": tok.get("id_token"), "access_token": tok["access_token"],
              "refresh_token": tok.get("refresh_token", ""), "token_type": tok.get("token_type", "Bearer"),
              "expires_at": time.time() + float(tok.get("expires_in") or 3600), "scopes": sorted(scopes), "saved_at": db.now()}
    regs = _registrations()
    regs[claims.get("sub", email)] = {"client_id": client_id, "email": email}
    db.set_setting("siwc_registrations", json.dumps(regs))
    name = f"ChatGPT ({email})" if email else "ChatGPT"
    existing = next((c for c in db.all("SELECT * FROM connections WHERE auth_kind = 'oauth_subscription'")
                     if _record(c).get("client_id") == client_id), None)
    if existing:
        cid = existing["id"]
        db.run("UPDATE connections SET state = 'connected', last_error = '', name = ? WHERE id = ?", name, cid)
    else:
        cid = db.run("INSERT INTO connections(name, auth_kind, protocol, base_url, secret_enc, residency, created_at) "
                     "VALUES (?, 'oauth_subscription', 'responses', ?, '', 'cloud', ?)", name, api_base(), db.now())
    store_connection_secret(cid, json.dumps(record))
    a.update(status="connected", connection_id=cid, email=email)
    for port in list(_servers):
        _close(port)
    return {"status": "connected", "connection_id": cid, "email": email}


def _record(conn: dict) -> dict:
    try:
        return json.loads(decrypt(conn["secret_enc"]) or "{}")
    except ValueError:
        return {}


def refresh(conn: dict, tokens: dict) -> str:
    """New access token from the rotating refresh token, or "" (the user must sign in again). Serialised (one refresh)."""
    with _refresh_lock:
        current = _record(db.one("SELECT * FROM connections WHERE id = ?", conn["id"]) or conn) or tokens
        if current.get("access_token") and (current.get("expires_at") or 0) > time.time() + 30:
            return current["access_token"]  # another request refreshed it while we waited
        form = {"grant_type": "refresh_token", "client_id": current.get("client_id", ""), "refresh_token": current.get("refresh_token", ""),
                "resource": resource()}
        try:
            r = httpx.post(f"{auth_base()}/api/accounts/oauth/token", data=form, timeout=15)
        except httpx.HTTPError:
            return ""
        if r.status_code >= 400:
            return ""
        tok = r.json()
        current.update(access_token=tok["access_token"], refresh_token=tok.get("refresh_token") or current.get("refresh_token", ""),
                       expires_at=time.time() + float(tok.get("expires_in") or 3600), saved_at=db.now())
        if tok.get("scope"):
            current["scopes"] = sorted(tok["scope"].split())
        if tok.get("id_token"):
            current["id_token"] = tok["id_token"]
        store_connection_secret(conn["id"], json.dumps(current))
        db.run("UPDATE connections SET state = 'connected', last_error = '' WHERE id = ?", conn["id"])
        return current["access_token"]


def sign_out(conn: dict) -> dict:
    """Revoke the refresh token, then clear tokens. The account's client registration is kept for the next sign-in."""
    rec = _record(conn)
    confirmed = False
    if rec.get("refresh_token"):
        try:
            endpoint = _discovery().get("revocation_endpoint")
            if endpoint:
                for _ in range(3):
                    r = httpx.post(endpoint, data={"token": rec["refresh_token"], "token_type_hint": "refresh_token",
                                                   "client_id": rec.get("client_id", "")}, timeout=10)
                    if r.status_code < 500:
                        confirmed = r.status_code == 200
                        break
                    time.sleep(1)
        except httpx.HTTPError:
            confirmed = False
    store_connection_secret(conn["id"], "")
    db.run("UPDATE connections SET state = 'needs_auth', last_error = 'Signed out' WHERE id = ?", conn["id"])
    return {"ok": True, "revoked": confirmed,
            "note": "" if confirmed else "Signed out here, but OpenAI didn't confirm it. You can also disconnect the app in ChatGPT Settings."}
