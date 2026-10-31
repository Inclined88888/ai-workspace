"""One-click mail and calendar: Connect Google and Connect Microsoft end to end against fake OAuth servers and fake
Gmail, Google Calendar and Microsoft Graph APIs (PKCE, loopback redirect, refresh, re-auth, disconnect and revoke),
mail and calendar through the ordinary pages and tools, approvals for sending and invitations, and the "Other
provider" auto-detection."""

import base64
import email
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from conftest import SCRIPT, _port, events, tool_call

fake = FastAPI()
S = {"valid": set(), "token_calls": [], "revoked": [], "sent": [], "drafts": [], "events": [], "replies": [],
     "refresh_ok": True, "expire_next": False, "google_scope": "", "ms_scope": ""}
NOW = datetime.now(timezone.utc).replace(microsecond=0)
GOOGLE_ALL = ("openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/gmail.readonly "
              "https://www.googleapis.com/auth/gmail.compose https://www.googleapis.com/auth/calendar.events")


def _authed(req: Request) -> bool:
    tok = req.headers.get("authorization", "").removeprefix("Bearer ")
    if S["expire_next"]:  # the provider rejects this one access token once (revoked early, clock skew)
        S["expire_next"] = False
        S["valid"].discard(tok)
    return tok in S["valid"]


def _deny():
    return JSONResponse({"error": {"code": 401, "message": "Invalid Credentials"}}, status_code=401)


def _issue(prefix: str) -> str:
    tok = f"{prefix}-{len(S['token_calls'])}"
    S["valid"].add(tok)
    return tok


# Google ------------------------------------------------------------------------------------------------------------
@fake.post("/google/token")
async def google_token(req: Request):
    form = dict((await req.form()).items())
    S["token_calls"].append({"provider": "google", **form})
    if form.get("client_id") != "gid.apps.googleusercontent.com" or form.get("client_secret") != "gsecret":
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    if form["grant_type"] == "authorization_code":
        if form["code"] != "g-code" or not form.get("code_verifier"):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return {"access_token": _issue("g-at"), "refresh_token": "g-rt", "expires_in": 3599, "scope": S["google_scope"] or GOOGLE_ALL}
    if form["grant_type"] == "refresh_token" and form["refresh_token"] == "g-rt" and S["refresh_ok"]:
        return {"access_token": _issue("g-at"), "expires_in": 3599}
    return JSONResponse({"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, status_code=400)


@fake.post("/google/revoke")
async def google_revoke(req: Request):
    S["revoked"].append(dict((await req.form()).items())["token"])
    return {}


@fake.get("/google/userinfo")
def google_userinfo(req: Request):
    return {"email": "sam@gmail.com", "name": "Sam Rivers"} if _authed(req) else _deny()


RAW = (b"From: Ana <ana@example.com>\r\nTo: sam@gmail.com\r\nSubject: Lunch on Friday?\r\nMessage-ID: <lunch-1@example.com>\r\n"
       b"Date: Thu, 29 Oct 2026 10:00:00 +0000\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nAre you free for lunch on Friday at 12?\r\n")
GMSG = {"id": "m1", "threadId": "t1", "labelIds": ["INBOX", "UNREAD"], "snippet": "Are you free for lunch", "internalDate": "1793263200000",
        "payload": {"headers": [{"name": "From", "value": "Ana <ana@example.com>"}, {"name": "Subject", "value": "Lunch on Friday?"},
                                {"name": "Date", "value": "Thu, 29 Oct 2026 10:00:00 +0000"}]}}


@fake.get("/gmail/v1/users/me/messages")
def gmail_list(req: Request, q: str = "", maxResults: int = 50):
    if not _authed(req):
        return _deny()
    if q.startswith("rfc822msgid:"):
        return {"messages": [{"id": "m1", "threadId": "t1"}] if q.endswith("lunch-1@example.com") else []}
    S.setdefault("queries", []).append(q)
    return {"messages": [{"id": "m1", "threadId": "t1"}]} if q in ("in:inbox", "lunch") else {"resultSizeEstimate": 0}


@fake.get("/gmail/v1/users/me/messages/{mid}")
def gmail_get(mid: str, req: Request, format: str = "full"):
    if not _authed(req):
        return _deny()
    if mid != "m1":
        return JSONResponse({"error": {"code": 404, "message": "Not Found"}}, status_code=404)
    if format == "raw":
        return {"id": "m1", "threadId": "t1", "raw": base64.urlsafe_b64encode(RAW).decode().rstrip("=")}
    assert req.query_params.getlist("metadataHeaders") == ["From", "Subject", "Date"]
    return GMSG


@fake.post("/gmail/v1/users/me/messages/send")
async def gmail_send(req: Request):
    if not _authed(req):
        return _deny()
    body = await req.json()
    msg = email.message_from_bytes(base64.urlsafe_b64decode(body["raw"] + "=="))
    S["sent"].append({"provider": "google", "to": msg["To"], "subject": msg["Subject"], "in_reply_to": msg["In-Reply-To"],
                      "thread": body.get("threadId"), "from": msg["From"], "body": msg.get_payload()})
    return {"id": "sent-1", "threadId": body.get("threadId", "t9")}


@fake.post("/gmail/v1/users/me/drafts")
async def gmail_draft(req: Request):
    if not _authed(req):
        return _deny()
    body = await req.json()
    msg = email.message_from_bytes(base64.urlsafe_b64decode(body["message"]["raw"] + "=="))
    S["drafts"].append({"provider": "google", "to": msg["To"], "subject": msg["Subject"], "thread": body["message"].get("threadId")})
    return {"id": "d1"}


@fake.get("/calendar/v3/calendars/primary/events")
def gcal_list(req: Request, pageToken: str = ""):
    if not _authed(req):
        return _deny()
    assert req.query_params["singleEvents"] == "true" and req.query_params["timeMin"]
    start = (NOW + timedelta(days=1)).replace(hour=3, minute=0, second=0)
    if not pageToken:
        return {"items": [{"id": "e1", "summary": "Standup", "start": {"dateTime": start.isoformat().replace("+00:00", "Z")},
                           "end": {"dateTime": (start + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")}},
                          {"id": "e2", "status": "cancelled", "summary": "Gone", "start": {"date": "2026-11-02"}}],
                "nextPageToken": "p2"}
    return {"items": [{"id": "e3", "summary": "Melbourne Cup", "start": {"date": "2026-11-03"}, "end": {"date": "2026-11-04"}}]}


@fake.post("/calendar/v3/calendars/primary/events")
async def gcal_insert(req: Request):
    if not _authed(req):
        return _deny()
    body = await req.json()
    S["events"].append({"provider": "google", "send_updates": req.query_params.get("sendUpdates"), **body})
    return {"id": f"new{len(S['events'])}", **body}


# Microsoft ---------------------------------------------------------------------------------------------------------
@fake.post("/ms/common/oauth2/v2.0/token")
async def ms_token(req: Request):
    form = dict((await req.form()).items())
    S["token_calls"].append({"provider": "microsoft", **form})
    if form.get("client_id") != "ms-client-id" or "client_secret" in form:  # a public client never sends a secret
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    if form["grant_type"] == "authorization_code":
        if form["code"] == "admin-needed":
            return JSONResponse({"error": "invalid_grant", "error_description": "AADSTS65001: The user or administrator has not consented"},
                                status_code=400)
        if form["code"] != "m-code" or not form.get("code_verifier") or "offline_access" not in form.get("scope", ""):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return {"access_token": _issue("m-at"), "refresh_token": "m-rt", "expires_in": 3600,
                "scope": S["ms_scope"] or "openid email profile https://graph.microsoft.com/Mail.ReadWrite "
                                          "https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/Calendars.ReadWrite "
                                          "https://graph.microsoft.com/User.Read"}
    if form["grant_type"] == "refresh_token" and form["refresh_token"] in ("m-rt", "m-rt2"):
        return {"access_token": _issue("m-at"), "refresh_token": "m-rt2", "expires_in": 3600}
    return JSONResponse({"error": "invalid_grant"}, status_code=400)


@fake.get("/graph/v1.0/me")
def graph_me(req: Request):
    return {"displayName": "Sam Rivers", "mail": None, "userPrincipalName": "sam@outlook.com"} if _authed(req) else _deny()


OMSG = {"id": "AAMk/abc+1=", "subject": "Quarterly plan", "from": {"emailAddress": {"name": "Lee", "address": "lee@contoso.com"}},
        "receivedDateTime": "2026-10-29T23:00:00Z", "isRead": False, "bodyPreview": "Can we meet next week?", "conversationId": "c1"}


@fake.get("/graph/v1.0/me/mailFolders/inbox/messages")
def graph_inbox(req: Request):
    return {"value": [OMSG]} if _authed(req) else _deny()


@fake.get("/graph/v1.0/me/messages")
def graph_messages(req: Request):
    if not _authed(req):
        return _deny()
    if "$filter" in req.query_params:
        return {"value": [{"id": OMSG["id"]}] if "plan-1@contoso.com" in req.query_params["$filter"] else []}
    assert req.query_params["$search"] == '"plan"'
    return {"value": [OMSG]}


@fake.get("/graph/v1.0/me/messages/{mid:path}")
def graph_message(mid: str, req: Request):
    if not _authed(req):
        return _deny()
    assert mid == OMSG["id"] and req.headers.get("prefer") == 'outlook.body-content-type="text"'
    return {**OMSG, "toRecipients": [{"emailAddress": {"address": "sam@outlook.com"}}], "ccRecipients": [],
            "internetMessageId": "<plan-1@contoso.com>", "body": {"contentType": "text", "content": "Can we meet next week?"}}


@fake.post("/graph/v1.0/me/messages/{mid:path}")
async def graph_message_action(mid: str, req: Request):
    if not _authed(req):
        return _deny()
    body = await req.json()
    if mid.endswith("/reply"):
        S["replies"].append({"id": mid[: -len("/reply")], **body})
        return Response(status_code=202)
    if mid.endswith("/createReply"):
        S["drafts"].append({"provider": "microsoft", "reply_to": mid[: -len("/createReply")], **body})
        return {"id": "draft-r1"}
    return JSONResponse({"error": {"message": "no"}}, status_code=400)


@fake.post("/graph/v1.0/me/messages")
async def graph_create_draft(req: Request):
    if not _authed(req):
        return _deny()
    S["drafts"].append({"provider": "microsoft", **(await req.json())})
    return {"id": "draft-1"}


@fake.post("/graph/v1.0/me/sendMail")
async def graph_send(req: Request):
    if not _authed(req):
        return _deny()
    S["sent"].append({"provider": "microsoft", **(await req.json())})
    return Response(status_code=202)


@fake.get("/graph/v1.0/me/calendarView")
def graph_calendar(req: Request):
    if not _authed(req):
        return _deny()
    assert req.headers.get("prefer") == 'outlook.timezone="UTC"'
    start = (NOW + timedelta(days=2)).replace(hour=0, minute=30, second=0)
    if "$skip" not in str(req.url):
        return {"value": [{"id": "ev1", "subject": "1:1 with Lee", "isAllDay": False, "isCancelled": False,
                           "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
                           "end": {"dateTime": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
                           "location": {"displayName": "Teams"}}],
                "@odata.nextLink": str(req.url.include_query_params(**{"$skip": "1"}))}
    return {"value": []}


@fake.post("/graph/v1.0/me/events")
async def graph_event(req: Request):
    if not _authed(req):
        return _deny()
    body = await req.json()
    S["events"].append({"provider": "microsoft", **body})
    return {"id": f"msev{len(S['events'])}"}


# Auto-detect -------------------------------------------------------------------------------------------------------
@fake.get("/ispdb/{domain}")
def ispdb(domain: str):
    if domain != "smallisp.test":
        return Response(status_code=404)
    return Response("""<clientConfig version="1.1"><emailProvider id="smallisp.test"><displayName>Small ISP</displayName>
      <incomingServer type="imap"><hostname>mail.smallisp.test</hostname><port>993</port><socketType>SSL</socketType></incomingServer>
      <outgoingServer type="smtp"><hostname>smtp.smallisp.test</hostname><port>587</port><socketType>STARTTLS</socketType></outgoingServer>
      </emailProvider></clientConfig>""", media_type="application/xml")


@fake.get("/doh")
def doh(name: str, type: str):
    mx = {"acme.test": "1 aspmx.l.google.com.", "contoso.test": "0 contoso-test.mail.protection.outlook.com."}.get(name)
    return {"Status": 0, "Answer": [{"name": name, "type": 15, "data": mx}] if mx else []}


@pytest.fixture(scope="module")
def fake_api():
    import socket

    port = _port()
    server = uvicorn.Server(uvicorn.Config(fake, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture
def env(client, fake_api, monkeypatch):
    u = fake_api
    for k, v in {"GOOGLE_AUTH_URL": f"{u}/google/auth", "GOOGLE_TOKEN_URL": f"{u}/google/token", "GOOGLE_REVOKE_URL": f"{u}/google/revoke",
                 "GOOGLE_USERINFO_URL": f"{u}/google/userinfo", "GMAIL_API_BASE": f"{u}/gmail/v1", "GCAL_API_BASE": f"{u}/calendar/v3",
                 "MICROSOFT_LOGIN_BASE": f"{u}/ms", "GRAPH_API_BASE": f"{u}/graph/v1.0",
                 "GOOGLE_OAUTH_CLIENT_ID": "gid.apps.googleusercontent.com", "GOOGLE_OAUTH_CLIENT_SECRET": "gsecret",
                 "MICROSOFT_OAUTH_CLIENT_ID": "ms-client-id", "MAIL_AUTODETECT_ONLINE": "0"}.items():
        monkeypatch.setenv(k, v)
    for key in ("valid", "token_calls", "revoked", "sent", "drafts", "events", "replies"):
        S[key].clear()
    S.update(refresh_ok=True, expire_next=False, google_scope="", ms_scope="")
    yield u
    from app import connect, db

    for a in db.all("SELECT id FROM linked_accounts"):
        connect.disconnect(a["id"])


def _connect(client, provider: str, code: str) -> dict:
    start = client.post(f"/api/connect/{provider}/start", json={}).json()
    q = {k: v[0] for k, v in parse_qs(urlparse(start["url"]).query).items()}
    r = httpx.get(f"{q['redirect_uri']}?code={code}&state={q['state']}", timeout=20)
    return {"start": start, "q": q, "page": r, "status": client.get(f"/api/connect/status/{q['state']}").json()}


def test_not_configured_is_explained_not_broken(client, monkeypatch):
    for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "MICROSOFT_OAUTH_CLIENT_ID"):
        monkeypatch.delenv(k, raising=False)
    info = client.get("/api/connect/providers").json()
    assert not info["google"]["configured"] and "app password" in info["google"]["message"]
    assert not info["microsoft"]["configured"] and info["microsoft"]["tenant"] == "common"
    r = client.post("/api/connect/google/start", json={})
    assert r.status_code == 409 and "isn't set up" in r.json()["detail"]
    # A client ID without the Desktop client's secret can't finish Google's token exchange, so it isn't "configured"
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "x.apps.googleusercontent.com")
    g = client.get("/api/connect/providers").json()["google"]
    assert not g["configured"] and "client secret" in g["message"]


def test_bring_your_own_registration_in_settings(client, monkeypatch):
    from app import db

    for k in ("MICROSOFT_OAUTH_CLIENT_ID", "MICROSOFT_OAUTH_TENANT"):
        monkeypatch.delenv(k, raising=False)
    try:
        out = client.put("/api/connect/microsoft/client", json={"client_id": "11111111-2222", "tenant": "consumers"}).json()
        assert out["configured"] and out["source"] == "settings" and out["tenant"] == "consumers"
        assert "11111111-2222" not in json.dumps(client.get("/api/connect/providers").json())  # only a hint goes back
        assert client.put("/api/connect/microsoft/client", json={"client_id": "x", "tenant": "bad/tenant"}).status_code == 422
    finally:
        client.put("/api/connect/microsoft/client", json={"client_id": "", "tenant": ""})
    assert db.get_setting("oauth_microsoft_client_id") == ""


def test_connect_google_sets_up_mail_and_calendar(client, env):
    from app import db
    from app.security import decrypt

    c = _connect(client, "google", "g-code")
    q = c["q"]
    assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/oauth/callback")
    assert q["code_challenge_method"] == "S256" and q["access_type"] == "offline" and q["prompt"] == "consent"
    assert set(q["scope"].split()) >= {"https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose",
                                       "https://www.googleapis.com/auth/calendar.events"}
    assert c["page"].status_code == 200 and "Connected." in c["page"].text
    st = c["status"]
    assert st["status"] == "connected" and st["email"] == "sam@gmail.com"
    ex = next(t for t in S["token_calls"] if t["grant_type"] == "authorization_code")
    assert hashlib.sha256(ex["code_verifier"].encode()).digest() == base64.urlsafe_b64decode(q["code_challenge"] + "==")
    assert ex["redirect_uri"] == q["redirect_uri"]
    # Tokens are stored encrypted (or in the keychain), never in plain text
    row = db.one("SELECT * FROM linked_accounts WHERE id = ?", st["account_id"])
    assert "g-rt" not in row["secret_enc"] and json.loads(decrypt(row["secret_enc"]))["refresh_token"] == "g-rt"
    accts = client.get("/api/connect/accounts").json()
    assert [(a["provider"], a["email"], a["status"]) for a in accts] == [("google", "sam@gmail.com", "connected")]
    mail_id, cal_id = accts[0]["mail_account_id"], accts[0]["calendar_id"]
    mailbox = next(a for a in client.get("/api/mail/accounts").json() if a["id"] == mail_id)
    assert mailbox["kind"] == "google" and "password_enc" not in mailbox
    cal = next(x for x in client.get("/api/calendars").json() if x["id"] == cal_id)
    assert cal["kind"] == "google" and cal["name"] == "Google Calendar · sam@gmail.com" and cal["last_synced"]
    # Calendar synced straight away, all pages, cancelled events dropped
    titles = {e["title"] for e in db.all("SELECT title FROM events WHERE calendar_id = ?", cal_id)}
    assert titles == {"Standup", "Melbourne Cup"}
    # Signing in again reuses the same account, mailbox and calendar
    again = _connect(client, "google", "g-code")["status"]
    assert again["account_id"] == st["account_id"] and len(client.get("/api/connect/accounts").json()) == 1


def test_gmail_read_search_send_and_drafts(client, env):
    st = _connect(client, "google", "g-code")["status"]
    aid = client.get("/api/connect/accounts").json()[0]["mail_account_id"]
    inbox = client.get(f"/api/mail/{aid}/messages").json()
    assert [(m["uid"], m["subject"], m["unread"]) for m in inbox] == [("m1", "Lunch on Friday?", True)]
    assert client.get(f"/api/mail/{aid}/messages", params={"q": "lunch"}).json()[0]["uid"] == "m1"
    assert client.get(f"/api/mail/{aid}/messages", params={"q": "nothing"}).json() == []
    msg = client.get(f"/api/mail/{aid}/messages/m1").json()
    assert msg["message_id"] == "<lunch-1@example.com>" and "lunch on Friday" in msg["body"] and msg["reply_to"] == "ana@example.com"
    # Reply from the Mail page: threaded into the Gmail conversation
    r = client.post(f"/api/mail/{aid}/send", json={"to": "ana@example.com", "subject": "Re: Lunch on Friday?", "body": "Yes, 12 works.",
                                                   "in_reply_to": msg["message_id"]})
    assert r.status_code == 200 and r.json()["ok"]
    sent = S["sent"][-1]
    assert sent["thread"] == "t1" and sent["in_reply_to"] == "<lunch-1@example.com>" and sent["from"] == "Sam Rivers <sam@gmail.com>"
    # Drafts saved by the assistant also land in Gmail Drafts
    from app import tools

    out = tools.save_draft(tools.DraftIn(to="ana@example.com", subject="Re: Lunch", body="Maybe", in_reply_to="<lunch-1@example.com>",
                                         account_id=aid))
    assert "Gmail Drafts" in out["status"] and S["drafts"][-1]["thread"] == "t1"
    assert client.get(f"/api/mail/{aid}/messages/nope").status_code == 502


def test_refresh_and_retry_and_sign_in_again(client, env):
    from app import connect, db

    st = _connect(client, "google", "g-code")["status"]
    la = st["account_id"]
    aid = client.get("/api/connect/accounts").json()[0]["mail_account_id"]
    # Near expiry: refreshed before the call (with the Desktop client's secret)
    rec = connect.record_of(la)
    rec["expires_at"] = time.time() - 5
    connect._store(la, rec)
    assert client.get(f"/api/mail/{aid}/messages").status_code == 200
    ref = S["token_calls"][-1]
    assert ref["grant_type"] == "refresh_token" and ref["client_secret"] == "gsecret" and ref["refresh_token"] == "g-rt"
    # The provider rejects a still-unexpired token: refresh once and retry
    S["expire_next"] = True
    n = len(S["token_calls"])
    assert client.get(f"/api/mail/{aid}/messages").status_code == 200 and len(S["token_calls"]) == n + 1
    # Refresh refused (revoked at Google, or a Testing-mode app's 7-day expiry): the account asks to sign in again
    S["refresh_ok"] = False
    S["expire_next"] = True
    r = client.get(f"/api/mail/{aid}/messages")
    assert r.status_code == 401 and "Connect it again" in r.json()["detail"]
    assert client.get("/api/connect/accounts").json()[0]["status"] == "reauth"
    S["refresh_ok"] = True
    assert _connect(client, "google", "g-code")["status"]["status"] == "connected"
    assert db.one("SELECT status FROM linked_accounts WHERE id = ?", la)["status"] == "connected"


def test_unticked_scopes_and_refusals(client, env):
    from app import db

    S["google_scope"] = "openid https://www.googleapis.com/auth/calendar.events"  # granular consent: calendar only
    st = _connect(client, "google", "g-code")["status"]
    acct = client.get("/api/connect/accounts").json()[0]
    assert st["status"] == "connected" and acct["calendar_id"] and acct["mail_account_id"] is None
    client.delete(f"/api/connect/accounts/{st['account_id']}")
    S["google_scope"] = "openid email"
    bad = _connect(client, "google", "g-code")
    assert bad["status"]["status"] == "failed" and "didn't tick" in bad["status"]["error"] and "tick the boxes" in bad["page"].text
    # Denied on the consent screen
    start = client.post("/api/connect/google/start", json={}).json()
    state = parse_qs(urlparse(start["url"]).query)["state"][0]
    out = client.post("/api/connect/complete", json={"url": f"http://127.0.0.1/oauth/callback?error=access_denied&state={state}"}).json()
    assert out["status"] == "failed" and "didn't allow" in out["error"]
    # A forged or stale state is refused
    assert client.post("/api/connect/complete", json={"url": "http://127.0.0.1/oauth/callback?code=g-code&state=forged"}).json()["status"] == "failed"
    assert not db.all("SELECT id FROM linked_accounts")


def test_connect_microsoft_mail_and_calendar(client, env):
    from app import db

    c = _connect(client, "microsoft", "m-code")
    q = c["q"]
    assert urlparse(c["start"]["url"]).path == "/ms/common/oauth2/v2.0/authorize"  # personal and work or school accounts
    assert q["redirect_uri"].startswith("http://localhost:") and "offline_access" in q["scope"] and "Mail.Send" in q["scope"]
    ex = next(t for t in S["token_calls"] if t["grant_type"] == "authorization_code")
    assert "client_secret" not in ex and ex["code_verifier"]
    st = c["status"]
    assert st["status"] == "connected" and st["email"] == "sam@outlook.com"  # personal accounts have no `mail`
    acct = client.get("/api/connect/accounts").json()[0]
    aid, cal_id = acct["mail_account_id"], acct["calendar_id"]
    inbox = client.get(f"/api/mail/{aid}/messages").json()
    assert inbox[0]["from"] == "Lee <lee@contoso.com>" and inbox[0]["unread"]
    assert client.get(f"/api/mail/{aid}/messages", params={"q": "plan"}).json()[0]["subject"] == "Quarterly plan"
    # Graph ids have / + = in them: they go through the path intact
    msg = client.get(f"/api/mail/{aid}/messages/AAMk%2Fabc%2B1%3D").json()
    assert msg["message_id"] == "<plan-1@contoso.com>" and msg["reply_to"] == "lee@contoso.com"
    client.post(f"/api/mail/{aid}/send", json={"to": "lee@contoso.com", "subject": "Re: Quarterly plan", "body": "Tuesday?",
                                               "in_reply_to": msg["message_id"]})
    assert S["replies"][-1]["id"] == OMSG["id"] and S["replies"][-1]["comment"] == "Tuesday?"
    client.post(f"/api/mail/{aid}/send", json={"to": "Kim <kim@example.com>, jo@example.com", "subject": "Hi", "body": "Hello"})
    sent = S["sent"][-1]["message"]
    assert [r["emailAddress"]["address"] for r in sent["toRecipients"]] == ["kim@example.com", "jo@example.com"]
    titles = [e["title"] for e in db.all("SELECT title FROM events WHERE calendar_id = ?", cal_id)]
    assert titles == ["1:1 with Lee"]
    ev = db.one("SELECT * FROM events WHERE calendar_id = ?", cal_id)
    assert ev["start"].endswith("+00:00") and ev["location"] == "Teams"
    # Microsoft has no revoke endpoint: disconnect removes everything here and says where to remove the app's access
    out = client.delete(f"/api/connect/accounts/{st['account_id']}").json()
    assert out["ok"] and "account.live.com" in out["note"]
    assert not db.all("SELECT id FROM mail_accounts WHERE kind = 'microsoft'") and not db.all("SELECT id FROM calendars WHERE id = ?", cal_id)


def test_work_account_needing_admin_consent(client, env):
    c = _connect(client, "microsoft", "admin-needed")
    assert c["status"]["status"] == "failed" and "administrator" in c["status"]["error"]
    start = client.post("/api/connect/microsoft/start", json={}).json()
    state = parse_qs(urlparse(start["url"]).query)["state"][0]
    url = f"http://localhost/oauth/callback?error=access_denied&error_description=AADSTS90094%3A+admin+approval&state={state}"
    assert "administrator" in client.post("/api/connect/complete", json={"url": url}).json()["error"]


def test_disconnect_google_revokes_and_deletes(client, env):
    from app import db

    st = _connect(client, "google", "g-code")["status"]
    acct = client.get("/api/connect/accounts").json()[0]
    assert client.delete(f"/api/mail/accounts/{acct['mail_account_id']}").status_code == 400  # disconnect is the one way out
    assert client.delete(f"/api/calendars/{acct['calendar_id']}").status_code == 400
    out = client.delete(f"/api/connect/accounts/{st['account_id']}").json()
    assert out["revoked"] and S["revoked"] == ["g-rt"]
    assert client.get("/api/connect/accounts").json() == []
    assert not db.one("SELECT id FROM mail_accounts WHERE id = ?", acct["mail_account_id"])
    assert not db.all("SELECT id FROM events WHERE calendar_id = ?", acct["calendar_id"])


def test_calendar_events_and_invitations(client, env):
    st = _connect(client, "google", "g-code")["status"]
    cal_id = client.get("/api/connect/accounts").json()[0]["calendar_id"]
    when = (NOW + timedelta(days=3)).replace(hour=0, minute=0, second=0)
    r = client.post("/api/events", json={"title": "Planning", "start": when.isoformat(), "end": (when + timedelta(hours=1)).isoformat(),
                                         "calendar_id": cal_id, "attendees": ["ana@example.com"], "location": "Cafe"})
    assert r.status_code == 200 and r.json()["calendar_id"] == cal_id
    g = S["events"][-1]
    assert g["send_updates"] == "all" and g["attendees"] == [{"email": "ana@example.com"}] and g["start"]["dateTime"] == when.isoformat()
    client.post("/api/events", json={"title": "Day off", "start": "2026-12-24", "all_day": True, "calendar_id": cal_id})
    assert S["events"][-1]["start"] == {"date": "2026-12-24"} and S["events"][-1]["send_updates"] == "none"
    # Invitations never come from the local calendar; with no calendar chosen they go from the connected one
    r = client.post("/api/events", json={"title": "Sync", "start": when.isoformat(), "attendees": ["kim@example.com"]})
    assert r.json()["calendar_id"] == cal_id
    # Microsoft: times go over as UTC, attendees as required
    _connect(client, "microsoft", "m-code")
    ms_cal = next(a for a in client.get("/api/connect/accounts").json() if a["provider"] == "microsoft")["calendar_id"]
    client.post("/api/events", json={"title": "Review", "start": "2026-11-05T10:00:00+10:00", "calendar_id": ms_cal, "attendees": ["lee@contoso.com"]})
    m = S["events"][-1]
    assert m["start"] == {"dateTime": "2026-11-05T00:00:00", "timeZone": "UTC"} and m["attendees"][0]["type"] == "required"
    # The Calendar page can rename or recolour a connected calendar (PUT sends its kind back)
    cal = next(x for x in client.get("/api/calendars").json() if x["id"] == cal_id)
    assert client.put(f"/api/calendars/{cal_id}", json={**cal, "name": "Work", "password": None}).status_code == 200
    assert client.post("/api/calendars", json={"name": "x", "kind": "google", "url": "https://x"}).status_code == 400


def test_assistant_send_and_invites_always_wait_for_approval(client, env):
    """Even in a clean conversation (nothing untrusted read), sending and inviting are held for the user's yes."""
    from app import db

    _connect(client, "google", "g-code")
    aid = client.get("/api/connect/accounts").json()[0]["mail_account_id"]
    when = (NOW + timedelta(days=4)).replace(hour=1, minute=0, second=0).isoformat()
    SCRIPT[:] = [
        tool_call("send_email", {"to": "ana@example.com", "subject": "Hello", "body": "Hi Ana", "account_id": aid}, "s"),
        tool_call("create_event", {"title": "Coffee", "start": when, "attendees": "ana@example.com, bo@example.com"}, "e"),
        tool_call("create_event", {"title": "Focus time", "start": when}, "f"),
        {"role": "assistant", "content": "I need your approval to send the email and the invitation."},
    ]
    n_sent, n_events = len(S["sent"]), len(S["events"])
    ev = events(client.post("/api/assistant/chat", json={"message": "Email Ana hello and invite her and Bo to coffee"}))
    held = [e for e in ev if e["type"] == "approval_required"]
    assert [h["tool"] for h in held] == ["send_email", "create_event"]
    assert "always needs your approval" in held[0]["reason"] and "Inviting people" in held[1]["reason"]
    assert len(S["sent"]) == n_sent and len(S["events"]) == n_events  # nothing left the machine
    assert db.one("SELECT id FROM events WHERE title = 'Focus time'")  # no attendees: an ordinary local event, no approval
    ok = client.post(f"/api/approvals/{held[0]['id']}", json={"approve": True}).json()
    assert ok["status"] == "done" and S["sent"][-1]["to"] == "ana@example.com" and S["sent"][-1]["subject"] == "Hello"
    client.post(f"/api/approvals/{held[1]['id']}", json={"approve": False})
    assert len(S["events"]) == n_events


def test_assistant_reads_and_drafts_with_microsoft(client, env):
    _connect(client, "microsoft", "m-code")
    aid = client.get("/api/connect/accounts").json()[0]["mail_account_id"]
    SCRIPT[:] = [tool_call("search_email", {"query": ""}, "a"),
                 tool_call("read_email", {"account_id": aid, "uid": OMSG["id"]}, "b"),
                 tool_call("save_email_draft", {"to": "lee@contoso.com", "subject": "Re: Quarterly plan", "body": "Tuesday at 10?",
                                                "in_reply_to": "<plan-1@contoso.com>", "account_id": aid}, "c"),
                 {"role": "assistant", "content": "Lee asks to meet next week. I saved a reply draft in Outlook."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "Summarise my inbox and draft a reply to Lee"}))
    results = {e["name"]: e for e in ev if e["type"] == "tool_result"}
    assert results["search_email"]["ok"] and "Quarterly plan" in results["search_email"]["preview"]
    assert results["read_email"]["ok"] and results["save_email_draft"]["ok"]
    assert S["drafts"][-1]["reply_to"] == OMSG["id"] and S["drafts"][-1]["comment"] == "Tuesday at 10?"


def test_find_free_time_skips_busy_slots(client, env):
    from app import assistant, db

    db.set_setting("timezone", "Australia/Brisbane")
    _connect(client, "google", "g-code")
    slots = assistant.free_time(30, 7, 9, 17)
    assert slots and all(datetime.fromisoformat(s["start"]).weekday() < 5 for s in slots)
    standup = db.one("SELECT start, end FROM events WHERE title = 'Standup'")
    s0, e0 = datetime.fromisoformat(standup["start"]), datetime.fromisoformat(standup["end"])
    for s in slots:
        a, b = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
        assert b - a >= timedelta(minutes=30) and (b <= s0 or a >= e0)
        assert 9 <= a.hour and (b.hour, b.minute) <= (17, 0)


def test_other_provider_autodetect(client, env, monkeypatch):
    det = lambda e: client.get("/api/mail/autodetect", params={"email": e}).json()  # noqa: E731
    icloud = det("me@icloud.com")
    assert icloud["imap_host"] == "imap.mail.me.com" and icloud["smtp_port"] == 587 and icloud["smtp_security"] == "starttls"
    assert icloud["app_password"] and icloud["caldav_url"] == "https://caldav.icloud.com" and "App-Specific" in " ".join(icloud["steps"])
    yahoo = det("x@yahoo.com.au")
    assert yahoo["imap_host"] == "imap.mail.yahoo.com" and yahoo["smtp_port"] == 465 and "app password" in " ".join(yahoo["steps"]).lower()
    assert det("me@fastmail.com")["caldav_url"].startswith("https://caldav.fastmail.com")
    assert det("me@gmail.com") == {"kind": "one_click", "provider": "google", "domain": "gmail.com"}
    assert det("me@hotmail.com")["provider"] == "microsoft"
    assert det("nope")["kind"] == "invalid"
    assert det("me@unknown.test")["kind"] == "guess" and det("me@unknown.test")["imap_host"] == "imap.unknown.test"
    # Online: MX shows Google Workspace or Microsoft 365, then Thunderbird's ISPDB
    monkeypatch.setenv("MAIL_AUTODETECT_ONLINE", "1")
    monkeypatch.setenv("ISPDB_URL", f"{env}/ispdb")
    monkeypatch.setenv("DOH_URL", f"{env}/doh")
    assert det("ceo@acme.test") == {"kind": "one_click", "provider": "google", "domain": "acme.test", "workspace": True}
    assert det("it@contoso.test")["provider"] == "microsoft"
    isp = det("me@smallisp.test")
    assert isp["imap_host"] == "mail.smallisp.test" and isp["smtp_security"] == "starttls" and isp["source"] == "ispdb"


def test_icloud_retries_with_the_short_username(client, monkeypatch):
    from fastapi import HTTPException

    from app import db, mail

    tried = []

    def fake_test(acct):
        tried.append(acct["username"])
        if "@" in acct["username"]:
            raise HTTPException(502, "Login failed")

    monkeypatch.setattr(mail, "test", fake_test)
    r = client.post("/api/mail/accounts", json={"name": "Me", "email": "me@icloud.com", "imap_host": "imap.mail.me.com", "imap_port": 993,
                                                "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_security": "starttls",
                                                "username": "me@icloud.com", "password": "abcd-efgh-ijkl-mnop"})
    assert r.status_code == 200 and tried == ["me@icloud.com", "me"]
    assert db.one("SELECT username, kind FROM mail_accounts WHERE id = ?", r.json()["id"]) == {"username": "me", "kind": "imap"}
    client.delete(f"/api/mail/accounts/{r.json()['id']}")


def test_existing_databases_gain_the_new_columns(tmp_path):
    import sqlite3

    from app import db

    c = sqlite3.connect(tmp_path / "old.db")
    c.executescript("CREATE TABLE mail_accounts (id INTEGER PRIMARY KEY, email TEXT); CREATE TABLE calendars (id INTEGER PRIMARY KEY, name TEXT);")
    db.ensure_columns(c)
    db.ensure_columns(c)  # idempotent
    assert {r[1] for r in c.execute("PRAGMA table_info(mail_accounts)")} >= {"kind", "linked_account_id"}
    assert "linked_account_id" in {r[1] for r in c.execute("PRAGMA table_info(calendars)")}
