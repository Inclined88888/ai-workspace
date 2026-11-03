"""One-click mail and calendar: "Connect Google" and "Connect Microsoft".

One sign-in sets up both mail and calendar for the account:

* OAuth 2.0 authorization code flow for native apps (RFC 8252): the system browser opens the provider's consent page,
  the redirect comes back to a one-shot listener on the loopback interface, PKCE (S256) and a fresh state per attempt.
  Google: http://127.0.0.1:<port>/oauth/callback (Desktop app client). Microsoft: http://localhost:<port>/oauth/callback
  by default, because Entra ignores the port only for registered localhost URIs and the portal can register that
  without editing the manifest (set MICROSOFT_REDIRECT_HOST=127.0.0.1 after adding it to replyUrlsWithType).
  Microsoft is a public client on the "common" tenant (personal and work or school accounts), with no secret.
* Tokens live in the OS keychain when the desktop app enabled it, otherwise Fernet-encrypted in SQLite, never in logs.
  Access tokens refresh shortly before they expire (one refresh at a time); a refused refresh marks the account
  "sign in again" instead of failing silently. Disconnect revokes at Google and deletes everything stored locally.
* The account then shows up as an ordinary mail account and an ordinary calendar, so the Mail page, the Calendar page,
  the assistant's tools and the MCP and OpenAPI tool servers all work with it unchanged (cloud_mail.py does the calls).

Client IDs: oauth_clients.py (build time), the environment, or a registration the user pastes in Settings. See
docs/connect-google-microsoft.md for how the maintainer creates them and what Google verification requires.
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
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import config, db, oauth_clients
from .security import decrypt, encrypt, require_user

log = logging.getLogger("workbench.connect")

GOOGLE_SCOPES = [
    "openid", "email", "profile",
    "https://www.googleapis.com/auth/gmail.readonly",   # read and search (restricted scope)
    "https://www.googleapis.com/auth/gmail.compose",    # drafts and sending (restricted scope)
    "https://www.googleapis.com/auth/calendar.events",  # read and create events (sensitive scope)
]
MICROSOFT_SCOPES = ["openid", "email", "profile", "offline_access", "User.Read",
                    "Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite"]
PROVIDERS = ("google", "microsoft")
CALLBACK_PATH = "/oauth/callback"
ATTEMPT_TTL = 600
_attempts: dict[str, dict] = {}
_servers: dict[int, list] = {}
_refresh_lock = threading.Lock()

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


# Configuration -----------------------------------------------------------------------------------------------------
def _url(env: str, default: str) -> str:
    return os.getenv(env, default).rstrip("/")


def client_config(provider: str) -> dict:
    """Which client this install uses: the environment, then one the user pasted in Settings, then the build's."""
    up = provider.upper()
    env_id = os.getenv(f"{up}_OAUTH_CLIENT_ID", "").strip()
    saved_id = (db.get_setting(f"oauth_{provider}_client_id", "") or "").strip()
    build_id = getattr(oauth_clients, f"{up}_CLIENT_ID", "").strip()
    if env_id:
        cid, secret, source = env_id, os.getenv(f"{up}_OAUTH_CLIENT_SECRET", ""), "environment"
    elif saved_id:
        cid, secret, source = saved_id, decrypt(db.get_setting(f"oauth_{provider}_client_secret_enc", "") or ""), "settings"
    elif build_id:
        cid, secret, source = build_id, getattr(oauth_clients, f"{up}_CLIENT_SECRET", ""), "build"
    else:
        cid, secret, source = "", "", "none"
    tenant = (os.getenv("MICROSOFT_OAUTH_TENANT") or db.get_setting("oauth_microsoft_tenant", "") or oauth_clients.MICROSOFT_TENANT
              or "common") if provider == "microsoft" else ""
    return {"client_id": cid, "client_secret": secret, "tenant": tenant, "source": source}


def endpoints(provider: str) -> dict:
    if provider == "google":
        return {"authorize": _url("GOOGLE_AUTH_URL", "https://accounts.google.com/o/oauth2/v2/auth"),
                "token": _url("GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token"),
                "revoke": _url("GOOGLE_REVOKE_URL", "https://oauth2.googleapis.com/revoke"),
                "host": "127.0.0.1", "scopes": GOOGLE_SCOPES,
                "extra": {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"}}
    tenant = client_config("microsoft")["tenant"]
    base = _url("MICROSOFT_LOGIN_BASE", "https://login.microsoftonline.com")
    return {"authorize": f"{base}/{tenant}/oauth2/v2.0/authorize", "token": f"{base}/{tenant}/oauth2/v2.0/token", "revoke": "",
            "host": os.getenv("MICROSOFT_REDIRECT_HOST", "localhost"), "scopes": MICROSOFT_SCOPES,
            "extra": {"prompt": "select_account"}}


NOT_CONFIGURED = {
    "google": ("One-click Google isn't set up in this build yet. You can still connect Gmail with an app password under "
               "Other provider, or paste your own Google OAuth client under Use your own app registration."),
    "microsoft": ("One-click Microsoft isn't set up in this build yet. Outlook.com and Microsoft 365 only allow OAuth, so "
                  "paste your own Azure app registration under Use your own app registration, or ask the maintainer."),
}


def providers_info() -> dict:
    out = {}
    for p in PROVIDERS:
        c = client_config(p)
        out[p] = {"configured": bool(c["client_id"]) and (p != "google" or bool(c["client_secret"])), "source": c["source"],
                  "client_id_hint": (c["client_id"][:10] + "…") if c["client_id"] else "", "tenant": c["tenant"],
                  "message": "" if c["client_id"] else NOT_CONFIGURED[p],
                  "scopes": endpoints(p)["scopes"]}
        if p == "google" and c["client_id"] and not c["client_secret"]:
            out[p]["message"] = ("This Google client ID has no client secret. Google's token endpoint requires the (non-confidential) "
                                 "secret of a Desktop app client; add it next to the client ID.")
    return out


# Loopback listener -------------------------------------------------------------------------------------------------
_PAGE = ("<!doctype html><meta charset=utf-8><title>AI Workspace</title><body style=\"font:16px/1.5 system-ui,sans-serif;"
         "background:#FFFDF6;color:#1d1b16;display:grid;place-items:center;height:100vh;margin:0\"><div style=\"max-width:460px\">"
         "<h1 style=\"font-weight:500\">{title}</h1><p>{body}</p></div>")


async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = (await asyncio.wait_for(reader.readline(), 10)).decode(errors="replace")
        while (await asyncio.wait_for(reader.readline(), 10)).strip():
            pass
        parts = line.split(" ")
        target = parts[1] if len(parts) > 1 else "/"
        if not target.startswith(CALLBACK_PATH):
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        else:
            res = await complete("http://127.0.0.1" + target)
            ok = res.get("status") == "connected"
            import html

            page = _PAGE.format(title="Connected." if ok else "That didn't finish.",
                                body=html.escape(f"{res.get('email', '')} is set up for mail and calendar. You can close this tab."
                                                 if ok else res.get("error", "Go back to AI Workspace and try again.")))
            data = page.encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\n"
                         b"Connection: close\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data)
        await writer.drain()
    except Exception:  # noqa: BLE001 - a stray request on the loopback must never take the app down
        log.warning("Mail and calendar sign-in callback failed", exc_info=False)
    finally:
        writer.close()


async def _listen() -> int:
    preferred = int(os.getenv("CONNECT_CALLBACK_PORT", "0") or 0)
    try:
        server = await asyncio.start_server(_serve, "127.0.0.1", preferred)
    except OSError:
        server = await asyncio.start_server(_serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    servers = [server]
    try:  # "localhost" can resolve to ::1 first; listen there too so the Microsoft redirect always lands
        servers.append(await asyncio.start_server(_serve, "::1", port))
    except OSError:
        pass
    _servers[port] = servers
    asyncio.get_running_loop().call_later(ATTEMPT_TTL, _close, port)
    return port


def _close(port: int) -> None:
    for s in _servers.pop(port, []):
        s.close()


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


# The flow ----------------------------------------------------------------------------------------------------------
async def start(provider: str, login_hint: str = "") -> dict:
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    info = providers_info()[provider]
    if not info["configured"]:
        raise HTTPException(409, info["message"])
    for st in [k for k, a in _attempts.items() if a["created"] < time.time() - ATTEMPT_TTL]:
        _attempts.pop(st, None)
    cfg, ep = client_config(provider), endpoints(provider)
    port = await _listen()
    redirect = f"http://{ep['host']}:{port}{CALLBACK_PATH}"
    verifier = _b64url(secrets.token_bytes(48))
    state = secrets.token_urlsafe(24)
    params = {"client_id": cfg["client_id"], "response_type": "code", "redirect_uri": redirect, "scope": " ".join(ep["scopes"]),
              "state": state, "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
              "code_challenge_method": "S256", **ep["extra"]}
    if login_hint:
        params["login_hint"] = login_hint
    _attempts[state] = {"provider": provider, "verifier": verifier, "redirect": redirect, "port": port, "created": time.time(),
                        "status": "pending", "error": "", "account_id": None, "email": ""}
    url = f"{ep['authorize']}?{urlencode(params)}"
    opened = False
    if config.DESKTOP and os.getenv("CONNECT_OPEN_BROWSER", "1") != "0":
        # Desktop app: the backend runs on this computer, so open the user's own default browser (RFC 8252 says the
        # system browser, never an embedded web view, which Google blocks and which could read the password)
        import webbrowser

        try:
            opened = await asyncio.to_thread(webbrowser.open, url)
        except Exception:  # noqa: BLE001 - fall back to the page opening it
            opened = False
    return {"url": url, "state": state, "redirect_uri": redirect, "opened": bool(opened)}


def status(state: str) -> dict:
    a = _attempts.get(state)
    if not a:
        return {"status": "unknown", "error": "This sign-in expired. Start again."}
    return {"status": a["status"], "error": a["error"], "account_id": a["account_id"], "email": a["email"], "provider": a["provider"]}


def _fail(a: dict, msg: str) -> dict:
    a.update(status="failed", error=msg)
    _close(a["port"])
    return {"status": "failed", "error": msg}


def _token_error(provider: str, r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        body = {}
    code = body.get("error", "") if isinstance(body, dict) else ""
    desc = (body.get("error_description", "") if isinstance(body, dict) else "")[:300]
    name = "Google" if provider == "google" else "Microsoft"
    if code == "invalid_client":
        return f"{name} rejected this app's client ID or secret. The maintainer needs to check the app registration."
    if "AADSTS65001" in desc or "AADSTS90094" in desc or code == "consent_required":
        return ("Your organisation needs an administrator to approve AI Workspace for mail and calendar. "
                "Ask your IT admin, or connect a personal account.")
    return f"{name} refused the sign-in ({code or r.status_code}). Try again."


async def complete(callback_url: str) -> dict:
    """Finish an attempt from the loopback redirect, or from the address-bar URL a user pastes (remote installs)."""
    q = {k: v[0] for k, v in parse_qs(urlparse(callback_url).query).items()}
    a = _attempts.get(q.get("state", ""))
    if not a:
        return {"status": "failed", "error": "This sign-in link doesn't match one started here. Start again."}
    if a["status"] != "pending":
        return status(q["state"])
    provider = a["provider"]
    if q.get("error"):
        desc = q.get("error_description", "")
        if "AADSTS90094" in desc or "AADSTS65001" in desc or q["error"] == "consent_required" or "admin" in desc.lower():
            return _fail(a, "Your organisation needs an administrator to approve AI Workspace for mail and calendar. "
                            "Ask your IT admin, or connect a personal account.")
        if q["error"] == "access_denied":
            return _fail(a, "You didn't allow access, so nothing was connected.")
        return _fail(a, f"Sign-in failed: {desc[:200] or q['error']}")
    if not q.get("code"):
        return _fail(a, "The sign-in reply had no authorization code.")
    cfg, ep = client_config(provider), endpoints(provider)
    form = {"grant_type": "authorization_code", "client_id": cfg["client_id"], "code": q["code"],
            "code_verifier": a["verifier"], "redirect_uri": a["redirect"]}
    if provider == "google":
        form["client_secret"] = cfg["client_secret"]
    else:
        form["scope"] = " ".join(ep["scopes"])
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(ep["token"], data=form, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return _fail(a, "Couldn't reach the sign-in server. Check your connection and try again.")
    if r.status_code >= 400:
        return _fail(a, _token_error(provider, r))
    tok = r.json()
    if not tok.get("access_token") or not tok.get("refresh_token"):
        return _fail(a, "The sign-in didn't return a long-lived token, so the connection would stop working within an hour. Try again.")
    granted = set((tok.get("scope") or " ".join(ep["scopes"])).split())
    from . import cloud_mail

    record = {"access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
              "expires_at": time.time() + float(tok.get("expires_in") or 3600), "scopes": sorted(granted),
              "client_id": cfg["client_id"], "saved_at": db.now()}
    try:
        me = await asyncio.to_thread(cloud_mail.whoami, provider, tok["access_token"])
    except HTTPException as e:
        return _fail(a, f"Signed in, but couldn't read the account's address: {e.detail}")
    mail_ok, cal_ok = cloud_mail.capabilities(provider, granted)
    if not mail_ok and not cal_ok:
        return _fail(a, "You signed in but didn't tick the boxes for mail or calendar, so there's nothing to connect. "
                        "Try again and allow both.")
    aid = save_account(provider, me["email"], me.get("name", ""), record, mail_ok, cal_ok)
    a.update(status="connected", account_id=aid, email=me["email"])
    _close(a["port"])
    try:  # first calendar sync now, so Home and the agenda show events straight away
        from . import calendar_sync

        for c in db.all("SELECT id FROM calendars WHERE linked_account_id = ?", aid):
            await asyncio.to_thread(calendar_sync.sync, c["id"])
    except HTTPException:
        pass
    return {"status": "connected", "account_id": aid, "email": me["email"], "mail": mail_ok, "calendar": cal_ok}


# Storage -----------------------------------------------------------------------------------------------------------
def _store(aid: int, record: dict) -> None:
    from . import keychain

    value = json.dumps(record)
    old = db.one("SELECT secret_enc FROM linked_accounts WHERE id = ?", aid)
    stored = keychain.put(f"linked-account:{aid}", value) if keychain.enabled() else encrypt(value)
    if old and old["secret_enc"] and keychain.is_ref(old["secret_enc"]) and not keychain.is_ref(stored):
        keychain.delete(old["secret_enc"])
    db.run("UPDATE linked_accounts SET secret_enc = ?, updated_at = ? WHERE id = ?", stored, db.now(), aid)


def record_of(aid: int) -> dict:
    row = db.one("SELECT secret_enc FROM linked_accounts WHERE id = ?", aid)
    try:
        return json.loads(decrypt(row["secret_enc"]) or "{}") if row else {}
    except ValueError:
        return {}


COLORS = {"google": "#1a73e8", "microsoft": "#0f6cbd"}
LABELS = {"google": ("Gmail", "Google Calendar"), "microsoft": ("Outlook", "Outlook Calendar")}
API_HOSTS = {"google": "gmail.googleapis.com", "microsoft": "graph.microsoft.com"}


def save_account(provider: str, email: str, name: str, record: dict, mail_ok: bool, cal_ok: bool) -> int:
    """Create or refresh the linked account and its mail account and calendar (signing in again reuses them)."""
    now = db.now()
    row = db.one("SELECT id FROM linked_accounts WHERE provider = ? AND lower(email) = lower(?)", provider, email)
    if row:
        aid = row["id"]
        db.run("UPDATE linked_accounts SET name = ?, status = 'connected', last_error = '', scopes = ?, updated_at = ? WHERE id = ?",
               name, " ".join(record["scopes"]), now, aid)
    else:
        aid = db.run("INSERT INTO linked_accounts(provider, email, name, scopes, secret_enc, status, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, '', 'connected', ?, ?)", provider, email, name, " ".join(record["scopes"]), now, now)
    _store(aid, record)
    mail_label, cal_label = LABELS[provider]
    if mail_ok and not db.one("SELECT id FROM mail_accounts WHERE linked_account_id = ?", aid):
        host = API_HOSTS[provider]
        db.run("INSERT INTO mail_accounts(name, email, imap_host, imap_port, smtp_host, smtp_port, smtp_security, username, password_enc, "
               "kind, linked_account_id) VALUES (?, ?, ?, 443, ?, 443, 'ssl', ?, '', ?, ?)",
               name or email, email, host, host, email, provider, aid)
    if cal_ok and not db.one("SELECT id FROM calendars WHERE linked_account_id = ?", aid):
        db.run("INSERT INTO calendars(name, kind, url, color, enabled, linked_account_id) VALUES (?, ?, '', ?, 1, ?)",
               f"{cal_label} · {email}", provider, COLORS[provider], aid)
    return aid


def mark_reauth(aid: int, why: str) -> None:
    db.run("UPDATE linked_accounts SET status = 'reauth', last_error = ? WHERE id = ?", why[:300], aid)


def access_token(aid: int, force: bool = False) -> str:
    """A valid access token for a linked account, refreshed when it is about to expire."""
    rec = record_of(aid)
    if not rec:
        raise HTTPException(401, "This account isn't connected any more. Connect it again in Settings.")
    if not force and rec.get("access_token") and (rec.get("expires_at") or 0) > time.time() + 60:
        return rec["access_token"]
    with _refresh_lock:
        rec = record_of(aid)
        if not force and rec.get("access_token") and (rec.get("expires_at") or 0) > time.time() + 60:
            return rec["access_token"]  # another request refreshed it while we waited
        acct = db.one("SELECT provider FROM linked_accounts WHERE id = ?", aid)
        provider = acct["provider"]
        cfg, ep = client_config(provider), endpoints(provider)
        form = {"grant_type": "refresh_token", "client_id": rec.get("client_id") or cfg["client_id"],
                "refresh_token": rec.get("refresh_token", "")}
        if provider == "google":
            form["client_secret"] = cfg["client_secret"]
        else:
            form["scope"] = " ".join(ep["scopes"])
        try:
            r = httpx.post(ep["token"], data=form, headers={"Accept": "application/json"}, timeout=20)
        except httpx.HTTPError as e:
            raise HTTPException(502, "Couldn't reach the sign-in server to refresh access. Try again shortly.") from e
        if r.status_code >= 400:
            mark_reauth(aid, _token_error(provider, r))
            raise HTTPException(401, f"Access to {db.one('SELECT email FROM linked_accounts WHERE id = ?', aid)['email']} "
                                     "has expired or was revoked. Connect it again in Settings.")
        tok = r.json()
        rec.update(access_token=tok["access_token"], refresh_token=tok.get("refresh_token") or rec.get("refresh_token", ""),
                   expires_at=time.time() + float(tok.get("expires_in") or 3600), saved_at=db.now())
        _store(aid, rec)
        db.run("UPDATE linked_accounts SET status = 'connected', last_error = '' WHERE id = ?", aid)
        return rec["access_token"]


def disconnect(aid: int) -> dict:
    from . import keychain

    acct = db.one("SELECT * FROM linked_accounts WHERE id = ?", aid)
    if not acct:
        raise HTTPException(404, "Account not found")
    revoked = False
    rec = record_of(aid)
    ep = endpoints(acct["provider"])
    if ep["revoke"] and rec.get("refresh_token"):
        try:
            r = httpx.post(ep["revoke"], data={"token": rec["refresh_token"]}, timeout=10)
            revoked = r.status_code < 400
        except httpx.HTTPError:
            revoked = False
    with db.tx() as t:
        t.execute("DELETE FROM mail_accounts WHERE linked_account_id = ?", (aid,))
        t.execute("DELETE FROM calendars WHERE linked_account_id = ?", (aid,))
        t.execute("DELETE FROM linked_accounts WHERE id = ?", (aid,))
    if keychain.is_ref(acct["secret_enc"]):
        keychain.delete(acct["secret_enc"])
    note = ("Access was revoked at Google too." if revoked else
            "Google didn't confirm the revocation; you can remove access at myaccount.google.com/permissions.")
    if acct["provider"] == "microsoft":
        note = ("Removed from this computer. To also remove the app's access, visit account.live.com/consent/Manage "
                "(personal) or myapps.microsoft.com (work or school).")
    return {"ok": True, "revoked": revoked, "note": note}


def accounts_out() -> list[dict]:
    rows = db.all("SELECT id, provider, email, name, scopes, status, last_error, created_at FROM linked_accounts ORDER BY id")
    for r in rows:
        r["mail_account_id"] = (db.one("SELECT id FROM mail_accounts WHERE linked_account_id = ?", r["id"]) or {}).get("id")
        r["calendar_id"] = (db.one("SELECT id FROM calendars WHERE linked_account_id = ?", r["id"]) or {}).get("id")
    return rows


# Routes ------------------------------------------------------------------------------------------------------------
class StartIn(BaseModel):
    login_hint: str = Field("", max_length=320)


class CompleteIn(BaseModel):
    url: str = Field(..., max_length=8000)


class ClientIn(BaseModel):
    client_id: str = Field("", max_length=300)
    client_secret: str | None = Field(None, max_length=300)
    tenant: str | None = Field(None, max_length=100, pattern=r"^[A-Za-z0-9.\-]*$")


@router.get("/connect/providers")
def providers_route():
    return providers_info()


@router.get("/connect/accounts")
def accounts_route():
    return accounts_out()


@router.post("/connect/{provider}/start")
async def start_route(provider: str, data: StartIn | None = None):
    return await start(provider, (data.login_hint if data else ""))


@router.get("/connect/status/{state}")
def status_route(state: str):
    return status(state)


@router.post("/connect/complete")
async def complete_route(data: CompleteIn):
    return await complete(data.url)


@router.delete("/connect/accounts/{aid}")
async def disconnect_route(aid: int):
    return await asyncio.to_thread(disconnect, aid)


@router.put("/connect/{provider}/client")
def set_client(provider: str, data: ClientIn):
    """Use your own Google OAuth client or Azure app registration (advanced; empty client_id goes back to the build's)."""
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    db.set_setting(f"oauth_{provider}_client_id", data.client_id.strip())
    if data.client_secret is not None or not data.client_id.strip():
        db.set_setting(f"oauth_{provider}_client_secret_enc", encrypt((data.client_secret or "").strip()))
    if provider == "microsoft" and data.tenant is not None:
        db.set_setting("oauth_microsoft_tenant", data.tenant.strip())
    return providers_info()[provider]
