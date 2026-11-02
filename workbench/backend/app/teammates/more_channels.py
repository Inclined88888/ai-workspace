"""More chat channels: Matrix, Signal, WhatsApp and email, alongside Slack, Discord and Telegram (channels.py).

Same rules as the others: only the people you list get answers, a message is a request from you answered in that same
chat, and nothing it asks for can send anything to anyone else without your OK (drafts, approvals; see channels.py).

Matrix: the official client-server API with an access token. Long-poll /sync, an outgoing connection, so no public URL.
  Answers in rooms an allowed user invites the bot to, and in direct chats. End-to-end encrypted rooms are not read
  (that needs the Olm/Megolm crypto stack); use an unencrypted room or DM.
Signal: Signal has no official bot API. The supported self-hosted route is signal-cli-rest-api
  (github.com/bbernhard/signal-cli-rest-api) run in MODE=native or normal, linked to your own Signal account as a
  device. Polled with GET /v1/receive/{number}, answered with POST /v2/send; outgoing only, no public URL.
WhatsApp: the official WhatsApp Business Cloud API. Meta only delivers messages to a public HTTPS webhook, so this needs
  a public URL for /hooks/whatsapp (a reverse proxy or a tunnel). Every delivery is checked against your app secret
  (X-Hub-Signature-256) before it is read. Replies go out through the Graph API from your business number.
Email: your connected mail account (Mail page). Mail from an address you list, with a subject that starts with the tag
  you pick (default "AI:"), is answered by a reply to that address. Only mail that arrives after you turn it on.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request, Response

from .. import db
from ..redact import redact
from ..security import decrypt, encrypt
from .channels import _with_notes, on_channel_conversation, teammate_for_channel

log = logging.getLogger("teammates.more_channels")
GRAPH_VERSION = "v23.0"  # WhatsApp Cloud API (Graph API); override with the whatsapp_graph_version setting
_state: dict[str, dict] = {name: {"running": False, "error": "", "account": ""} for name in ("matrix", "signal", "whatsapp", "email")}

MATRIX_STEPS = [
    "Create a Matrix account for the bot on your homeserver (for example a new account on matrix.org).",
    "Get its access token: in Element, sign in as the bot, then Settings, Help & About, Access Token.",
    "Paste the homeserver URL (for example https://matrix.org) and the token here, then your own Matrix ID (@you:server).",
    "Invite the bot to a direct chat from your account. It joins rooms only when an allowed user invites it.",
]
SIGNAL_STEPS = [
    "Run signal-cli-rest-api on your computer or server (docker run -p 8080:8080 -e MODE=native "
    "-v $HOME/.local/share/signal-api:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api).",
    "Open http://localhost:8080/v1/qrcodelink?device_name=ai-workspace and scan it in Signal: Settings, Linked devices.",
    "Paste the API URL and your Signal number (+61...) here, then the numbers allowed to ask (yours).",
    "Message yourself (Note to Self) or have an allowed number message you; the bot answers in that chat.",
]
WHATSAPP_STEPS = [
    "In developers.facebook.com create an app with WhatsApp, and note the Phone number ID and a permanent access token.",
    "Copy the App secret (App settings, Basic) and choose any verify token.",
    "Give this server a public HTTPS address and set the webhook to https://<your address>/hooks/whatsapp with that "
    "verify token; subscribe to messages.",
    "Paste the phone number ID, access token, app secret and verify token here, then the WhatsApp numbers allowed to ask.",
]
EMAIL_STEPS = [
    "Connect your mail on the Mail page first.",
    "Pick the account, the addresses allowed to ask (usually just yours) and a subject tag.",
    "Send an email to that account with a subject starting with the tag (for example AI: what's on tomorrow?). "
    "The answer comes back as a reply.",
]


def _get(name: str) -> str:
    return db.get_setting(name, "") or ""


def _secret(name: str) -> str:
    return decrypt(db.get_setting(f"secret:{name}", "") or "")


def _set_secret(name: str, value: str | None) -> None:
    if value is not None:
        db.set_setting(f"secret:{name}", encrypt(value.strip()) if value.strip() else "")


def _allowed(channel: str, normalise=lambda x: x) -> set[str]:
    return {normalise(x.strip()) for x in (db.get_setting(f"{channel}_allowed_users", "") or "").split(",") if x.strip()}


def _set_allowed(channel: str, value: str | None, check=None, normalise=lambda x: x) -> None:
    if value is None:
        return
    ids = {normalise(x.strip()) for x in value.split(",") if x.strip()}
    if check:
        bad = [x for x in ids if not check(x)]
        if bad:
            raise HTTPException(400, f"Not a valid id for this channel: {', '.join(sorted(bad))}")
    db.set_setting(f"{channel}_allowed_users", ",".join(sorted(ids)))


def _set_teammate(channel: str, teammate_id: int | None) -> None:
    from . import store

    if teammate_id is not None:
        store.get(teammate_id)
        db.set_setting(f"{channel}_teammate_id", str(teammate_id))


def _conversation(channel: str, key: str, title: str) -> int:
    from .. import assistant

    c = db.one("SELECT id FROM conversations WHERE channel = ? AND external_id = ? ORDER BY id DESC LIMIT 1", channel, key)
    if c:
        return c["id"]
    cid = assistant.new_conversation(channel, title, key)
    on_channel_conversation(cid, channel)
    return cid


async def _answer(channel: str, key: str, title: str, request_id: str, text: str) -> str | None:
    """One message -> one teammate turn. None when this message id was already answered (redelivery, restart)."""
    from .. import actions, assistant

    if request_id and actions.by_key("turn:" + request_id):
        return None
    ctx = assistant.Ctx(channel=channel, conversation_id=_conversation(channel, key, title), request_key=request_id)
    reply, events = await assistant.ask(ctx, text)
    return _with_notes(reply, events, ctx.conversation_id) or "(no reply)"


def status() -> dict:
    def common(ch: str) -> dict:
        return {"running": _state[ch]["running"], "error": _state[ch]["error"], "account": _state[ch]["account"],
                "allowed_users": db.get_setting(f"{ch}_allowed_users", "") or "", "teammate": teammate_for_channel(ch)["id"]}
    return {
        "matrix": {**common("matrix"), "configured": matrix_configured(), "homeserver": db.get_setting("matrix_homeserver", "") or "",
                   "steps": MATRIX_STEPS},
        "signal": {**common("signal"), "configured": signal_configured(), "api_url": db.get_setting("signal_api_url", "") or "",
                   "number": db.get_setting("signal_number", "") or "", "steps": SIGNAL_STEPS},
        "whatsapp": {**common("whatsapp"), "available": True, "configured": whatsapp_configured(),
                     "phone_number_id": db.get_setting("whatsapp_phone_number_id", "") or "", "webhook_path": "/hooks/whatsapp",
                     "steps": WHATSAPP_STEPS},
        "email": {**common("email"), "configured": email_configured(), "account_id": db.get_setting("email_account_id", "") or "",
                  "subject_tag": email_tag(), "steps": EMAIL_STEPS},
    }


# Matrix -------------------------------------------------------------------------------------------------------------
def matrix_configured() -> bool:
    return bool(db.get_setting("matrix_homeserver", "") and _secret("MATRIX_ACCESS_TOKEN"))


def configure_matrix(homeserver: str | None, access_token: str | None, allowed_users: str | None, teammate_id: int | None) -> dict:
    if homeserver is not None:
        hs = homeserver.strip().rstrip("/")
        if hs and not hs.startswith(("https://", "http://")):
            raise HTTPException(400, "The homeserver URL starts with https://")
        db.set_setting("matrix_homeserver", hs)
        db.set_setting("matrix_since", "")
    _set_secret("MATRIX_ACCESS_TOKEN", access_token)
    _set_allowed("matrix", allowed_users, check=lambda x: x.startswith("@") and ":" in x)
    _set_teammate("matrix", teammate_id)
    return status()["matrix"]


def _mx(path: str) -> str:
    return db.get_setting("matrix_homeserver", "").rstrip("/") + "/_matrix/client/v3" + path


def _mx_headers() -> dict:
    return {"Authorization": f"Bearer {_secret('MATRIX_ACCESS_TOKEN')}"}


async def test_matrix() -> dict:
    if not matrix_configured():
        raise HTTPException(400, "Add the homeserver and access token first")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(_mx("/account/whoami"), headers=_mx_headers())
    if r.status_code != 200:
        raise HTTPException(400, f"Matrix said: {r.status_code} ({'token rejected' if r.status_code == 401 else 'error'})")
    _state["matrix"]["account"] = r.json().get("user_id", "")
    return {"ok": True, "user_id": _state["matrix"]["account"]}


async def matrix_send(room: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=30) as c:
        for i in range(0, len(text or " "), 30_000):
            r = await c.put(_mx(f"/rooms/{quote(room, safe='')}/send/m.room.message/{uuid.uuid4().hex}"), headers=_mx_headers(),
                            json={"msgtype": "m.text", "body": text[i:i + 30_000] or " "})
            if r.status_code >= 400:
                raise RuntimeError(f"Matrix send failed: {r.status_code}")


async def handle_matrix_sync(data: dict, me: str) -> list[str]:
    """One /sync response: join rooms an allowed user invited the bot to, answer their messages. Returns the replies."""
    allowed, replies = _allowed("matrix"), []
    rooms = data.get("rooms") or {}
    async with httpx.AsyncClient(timeout=30) as c:
        for room, inv in (rooms.get("invite") or {}).items():
            inviter = next((e.get("sender") for e in (inv.get("invite_state") or {}).get("events") or []
                            if e.get("type") == "m.room.member" and e.get("state_key") == me), "")
            if inviter in allowed:
                await c.post(_mx(f"/join/{quote(room, safe='')}"), headers=_mx_headers(), json={})
    for room, joined in (rooms.get("join") or {}).items():
        for ev in (joined.get("timeline") or {}).get("events") or []:
            if ev.get("type") != "m.room.message" or ev.get("sender") == me:
                continue
            body = ((ev.get("content") or {}).get("body") or "").strip()
            if ev.get("sender") not in allowed or not body:
                continue
            reply = await _answer("matrix", f"matrix:{room}", "Matrix chat", f"matrix:{ev.get('event_id', '')}", body)
            if reply:
                await matrix_send(room, reply)
                replies.append(reply)
    return replies


async def matrix_loop() -> None:
    while True:
        if not matrix_configured():
            _state["matrix"].update(running=False, error="")
            await asyncio.sleep(10)
            continue
        try:
            me = (await test_matrix())["user_id"]
            since = db.get_setting("matrix_since", "") or ""
            if not since:  # first start: skip history, answer only what arrives from now on
                async with httpx.AsyncClient(timeout=40) as c:
                    r = await c.get(_mx("/sync"), headers=_mx_headers(), params={"timeout": 0})
                r.raise_for_status()
                since = r.json().get("next_batch", "")
                db.set_setting("matrix_since", since)
            _state["matrix"].update(running=True, error="")
            while matrix_configured():
                async with httpx.AsyncClient(timeout=60) as c:
                    r = await c.get(_mx("/sync"), headers=_mx_headers(), params={"since": since, "timeout": 30000})
                r.raise_for_status()
                data = r.json()
                try:
                    await handle_matrix_sync(data, me)
                except Exception as e:  # noqa: BLE001 - one bad message must not stop the bot
                    log.warning("matrix message failed: %s", e)
                since = data.get("next_batch", since)
                db.set_setting("matrix_since", since)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _state["matrix"].update(running=False, error=redact(str(e))[:300])
            await asyncio.sleep(15)


# Signal (signal-cli-rest-api) ----------------------------------------------------------------------------------------
def signal_configured() -> bool:
    return bool(db.get_setting("signal_api_url", "") and db.get_setting("signal_number", ""))


def _e164(x: str) -> bool:
    return x.startswith("+") and x[1:].isdigit() and 8 <= len(x) <= 16


def configure_signal(api_url: str | None, number: str | None, allowed_users: str | None, teammate_id: int | None) -> dict:
    if api_url is not None:
        u = api_url.strip().rstrip("/")
        if u and not u.startswith(("http://", "https://")):
            raise HTTPException(400, "The API URL starts with http:// (for example http://localhost:8080)")
        db.set_setting("signal_api_url", u)
    if number is not None:
        n = number.strip().replace(" ", "")
        if n and not _e164(n):
            raise HTTPException(400, "Your Signal number in international format, for example +61412345678")
        db.set_setting("signal_number", n)
    _set_allowed("signal", allowed_users, check=_e164, normalise=lambda x: x.replace(" ", ""))
    _set_teammate("signal", teammate_id)
    return status()["signal"]


async def test_signal() -> dict:
    if not signal_configured():
        raise HTTPException(400, "Add the API URL and your number first")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(_get("signal_api_url") + "/v1/about")
    if r.status_code != 200:
        raise HTTPException(400, f"signal-cli-rest-api answered {r.status_code}")
    about = r.json()
    if "json-rpc" in str(about.get("mode", "")):
        raise HTTPException(400, "Run signal-cli-rest-api with MODE=native or MODE=normal (json-rpc mode only offers a websocket)")
    return {"ok": True, "mode": about.get("mode", ""), "version": about.get("version", "")}


async def signal_send(to: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(_get("signal_api_url") + "/v2/send",
                         json={"message": text or " ", "number": _get("signal_number"), "recipients": [to]})
    if r.status_code >= 400:
        raise RuntimeError(f"Signal send failed: {r.status_code}")


async def handle_signal_envelopes(items: list) -> list[str]:
    """Envelopes from /v1/receive: answer text from allowed numbers in the same chat (Note to Self included)."""
    me, allowed, replies = db.get_setting("signal_number", ""), _allowed("signal"), []
    for item in items or []:
        env = (item or {}).get("envelope") or {}
        sender = env.get("sourceNumber") or env.get("source") or ""
        data = env.get("dataMessage") or {}
        sync = ((env.get("syncMessage") or {}).get("sentMessage") or {})
        if sync and sender == me and (sync.get("destinationNumber") or sync.get("destination")) == me:
            data = sync  # a message you wrote in Note to Self on your phone
        text = (data.get("message") or "").strip()
        if not text or sender not in allowed or data.get("groupInfo"):
            continue
        stamp = data.get("timestamp") or env.get("timestamp") or ""
        reply = await _answer("signal", f"signal:{sender}", "Signal chat", f"signal:{sender}:{stamp}", text)
        if reply:
            await signal_send(sender, reply)
            replies.append(reply)
    return replies


async def signal_loop() -> None:
    while True:
        if not signal_configured():
            _state["signal"].update(running=False, error="")
            await asyncio.sleep(10)
            continue
        try:
            url = f"{_get('signal_api_url')}/v1/receive/{quote(_get('signal_number'), safe='')}"
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.get(url, params={"timeout": 20, "ignore_attachments": "true", "ignore_stories": "true"})
            r.raise_for_status()
            _state["signal"].update(running=True, error="", account=db.get_setting("signal_number", ""))
            try:
                await handle_signal_envelopes(r.json())
            except Exception as e:  # noqa: BLE001
                log.warning("signal message failed: %s", e)
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _state["signal"].update(running=False, error=redact(str(e))[:300])
            await asyncio.sleep(15)


# WhatsApp (Cloud API, webhook) --------------------------------------------------------------------------------------
def whatsapp_configured() -> bool:
    return bool(db.get_setting("whatsapp_phone_number_id", "") and _secret("WHATSAPP_TOKEN") and _secret("WHATSAPP_APP_SECRET")
                and _secret("WHATSAPP_VERIFY_TOKEN"))


def configure_whatsapp(phone_number_id: str | None, access_token: str | None, app_secret: str | None, verify_token: str | None,
                       allowed_users: str | None, teammate_id: int | None) -> dict:
    if phone_number_id is not None:
        if phone_number_id.strip() and not phone_number_id.strip().isdigit():
            raise HTTPException(400, "The phone number ID is a number (WhatsApp, API Setup in your Meta app)")
        db.set_setting("whatsapp_phone_number_id", phone_number_id.strip())
    _set_secret("WHATSAPP_TOKEN", access_token)
    _set_secret("WHATSAPP_APP_SECRET", app_secret)
    _set_secret("WHATSAPP_VERIFY_TOKEN", verify_token)
    _set_allowed("whatsapp", allowed_users, check=lambda x: x.isdigit() and 8 <= len(x) <= 15,
                 normalise=lambda x: x.replace(" ", "").lstrip("+"))
    _set_teammate("whatsapp", teammate_id)
    _state["whatsapp"]["running"] = whatsapp_configured()
    return status()["whatsapp"]


def whatsapp_verify(mode: str, token: str, challenge: str) -> Response:
    """Meta's webhook check (GET): echo the challenge only for the verify token you chose."""
    expected = _secret("WHATSAPP_VERIFY_TOKEN")
    if mode == "subscribe" and expected and hmac.compare_digest(token or "", expected):
        return Response(challenge, media_type="text/plain")
    raise HTTPException(403, "verify token mismatch")


def whatsapp_signature_ok(body: bytes, header: str) -> bool:
    secret = _secret("WHATSAPP_APP_SECRET")
    if not secret or not header.startswith("sha256="):
        return False
    return hmac.compare_digest(hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), header[7:])


async def whatsapp_send(to: str, text: str) -> None:
    version = db.get_setting("whatsapp_graph_version", "") or GRAPH_VERSION
    url = f"https://graph.facebook.com/{version}/{_get('whatsapp_phone_number_id')}/messages"
    async with httpx.AsyncClient(timeout=30) as c:
        for i in range(0, len(text or " "), 4000):
            r = await c.post(url, headers={"Authorization": f"Bearer {_secret('WHATSAPP_TOKEN')}"},
                             json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text[i:i + 4000] or " "}})
            if r.status_code >= 400:
                raise RuntimeError(f"WhatsApp send failed: {r.status_code}")


async def handle_whatsapp(payload: dict) -> list[str]:
    allowed, replies = _allowed("whatsapp"), []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            for m in (change.get("value") or {}).get("messages") or []:
                sender, text = str(m.get("from", "")), ((m.get("text") or {}).get("body") or "").strip()
                if m.get("type") != "text" or not text or sender not in allowed:
                    continue
                reply = await _answer("whatsapp", f"whatsapp:{sender}", "WhatsApp chat", f"whatsapp:{m.get('id', '')}", text)
                if reply:
                    await whatsapp_send(sender, reply)
                    replies.append(reply)
    return replies


async def whatsapp_webhook(request: Request) -> dict:
    """Meta's POST: refused unless signed with your app secret; answered after the 200 so Meta doesn't retry."""
    if not whatsapp_configured():
        raise HTTPException(404, "WhatsApp is not set up")
    body = await request.body()
    if not whatsapp_signature_ok(body, request.headers.get("x-hub-signature-256", "")):
        raise HTTPException(401, "bad signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "not JSON") from None

    async def run() -> None:
        try:
            await handle_whatsapp(payload)
        except Exception as e:  # noqa: BLE001
            _state["whatsapp"]["error"] = redact(str(e))[:300]
            log.warning("whatsapp message failed: %s", e)
    asyncio.create_task(run())
    return {"ok": True}


# Email --------------------------------------------------------------------------------------------------------------
def email_tag() -> str:
    return db.get_setting("email_subject_tag", "") or "AI:"


def email_configured() -> bool:
    return bool(db.get_setting("email_account_id", "") and db.get_setting("email_allowed_users", ""))


def configure_email(account_id: int | None, allowed_users: str | None, subject_tag: str | None, teammate_id: int | None) -> dict:
    if account_id is not None:
        if account_id and not db.one("SELECT id FROM mail_accounts WHERE id = ?", account_id):
            raise HTTPException(404, "No such mail account: connect it on the Mail page first")
        db.set_setting("email_account_id", str(account_id or ""))
        db.set_setting("email_since", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()))  # only mail from now on
    _set_allowed("email", allowed_users, check=lambda x: "@" in x and " " not in x, normalise=lambda x: x.lower())
    if subject_tag is not None:
        db.set_setting("email_subject_tag", subject_tag.strip()[:40] or "AI:")
    _set_teammate("email", teammate_id)
    return status()["email"]


def _utc(iso: str) -> str:
    """A message date as naive UTC text, comparable with email_since."""
    from datetime import datetime, timezone

    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:19]
    if d.tzinfo:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def _address(from_header: str) -> str:
    from email.utils import parseaddr

    return parseaddr(from_header or "")[1].lower()


async def handle_email_once() -> list[str]:
    """New tagged mail from allowed senders since it was turned on: one reply per message, never twice."""
    from .. import mail

    acct = db.one("SELECT * FROM mail_accounts WHERE id = ?", int(db.get_setting("email_account_id", "0") or 0))
    if not acct:
        return []
    allowed, tag, since = _allowed("email", lambda x: x.lower()), email_tag().lower(), db.get_setting("email_since", "") or ""
    done = set(json.loads(db.get_setting("email_answered", "[]") or "[]"))
    msgs = await asyncio.to_thread(mail.list_messages, acct, "INBOX", 30, "")
    replies = []
    for m in reversed(msgs):
        if m["uid"] in done or not m.get("subject", "").lower().startswith(tag) or _address(m.get("from", "")) not in allowed:
            continue
        if since and m.get("date") and _utc(m["date"]) < since:
            continue
        full = await asyncio.to_thread(mail.get_message, acct, m["uid"])
        sender, subject = _address(full.get("from", "")), full.get("subject", "")
        question = f"{subject[len(tag):].strip()}\n\n{(full.get('body') or '').strip()}".strip()
        reply = await _answer("email", f"email:{sender}", "Email chat", f"email:{full.get('message_id') or m['uid']}", question)
        done.add(m["uid"])
        db.set_setting("email_answered", json.dumps(sorted(done)[-500:]))
        if reply:
            re_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
            await asyncio.to_thread(mail.send, acct, sender, re_subject, reply, full.get("message_id") or "")
            replies.append(reply)
    return replies


async def email_loop() -> None:
    while True:
        if not email_configured():
            _state["email"].update(running=False, error="")
            await asyncio.sleep(15)
            continue
        try:
            await handle_email_once()
            _state["email"].update(running=True, error="")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _state["email"].update(running=False, error=redact(str(getattr(e, "detail", e)))[:300])
        await asyncio.sleep(60)
