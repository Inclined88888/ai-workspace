"""Chat channels: talk to your teammates from Slack, Discord and Telegram (feat/grok-style).

Slack uses Socket Mode, so like the Telegram gateway it needs no public URL: the app opens an outgoing WebSocket to
Slack. Setup is a short wizard: create the app from our manifest (one paste), install it, paste the two tokens, pick which
teammate answers. Only the Slack users you list get answers.
Discord uses the Gateway (also an outgoing WebSocket, no public URL). It answers DMs and messages that @mention the bot,
which Discord delivers with their text without the privileged Message Content intent, so setup is: create the app,
copy the bot token, invite it with the generated link. Only the Discord user IDs you list get answers.

Safety: a message that arrives on a channel is a request from you, answered in that same chat. It can never make a
teammate send anything to anyone else: outgoing messages are drafts (outbox) that you send from the app or phone, and
email/invites still need your approval every time.
Matrix, Signal, WhatsApp and email live in more_channels.py.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from fastapi import HTTPException

from .. import db
from ..redact import redact
from ..security import decrypt, encrypt
from . import store

log = logging.getLogger("teammates.channels")
SLACK = "https://slack.com/api"
_slack = {"running": False, "team": "", "bot": "", "error": ""}
DISCORD = "https://discord.com/api/v10"
DISCORD_INTENTS = (1 << 9) | (1 << 12)  # GUILD_MESSAGES | DIRECT_MESSAGES; no privileged intents needed
DISCORD_PERMISSIONS = 2048 | 1024 | 65536  # Send Messages, View Channels, Read Message History
_discord = {"running": False, "bot": "", "bot_id": "", "app_id": "", "error": ""}

SLACK_MANIFEST = {
    "display_information": {"name": "AI Workspace", "description": "Your AI Workspace teammates, in Slack",
                            "background_color": "#1d1a14"},
    "features": {"bot_user": {"display_name": "AI Workspace", "always_online": True},
                 "app_home": {"messages_tab_enabled": True, "messages_tab_read_only_enabled": False}},
    "oauth_config": {"scopes": {"bot": ["chat:write", "im:history", "im:read", "im:write", "app_mentions:read", "users:read"]}},
    "settings": {"event_subscriptions": {"bot_events": ["message.im", "app_mention"]}, "socket_mode_enabled": True,
                 "org_deploy_enabled": False, "token_rotation_enabled": False},
}
SLACK_STEPS = [
    "Open api.slack.com/apps and choose Create New App, then From an app manifest.",
    "Pick your workspace, paste the manifest below, and choose Create.",
    "Under Basic Information, App-Level Tokens, choose Generate Token, add the connections:write scope, and copy the token (starts with xapp-).",
    "Under Install App, choose Install to Workspace, then copy the Bot User OAuth Token (starts with xoxb-).",
    "Paste both tokens here, add your Slack member ID (in Slack: your profile, ⋯, Copy member ID), and pick who answers.",
]
DISCORD_STEPS = [
    "Open discord.com/developers/applications and choose New Application. Name it (for example AI Workspace).",
    "Open Bot, choose Reset Token, and copy the bot token.",
    "Paste the token here and save. An invite link appears: open it to add the bot to your server (DMs work without a server).",
    "Add your Discord user ID (Settings, Advanced, Developer Mode on; then right-click your name, Copy User ID), and pick who answers.",
]
TELEGRAM_STEPS = [
    "In Telegram, open @BotFather and send /newbot. Pick a name and a username ending in bot.",
    "Copy the token BotFather gives you and paste it here.",
    "Send any message to your new bot. It replies with your Telegram user ID: paste that here so only you get answers.",
]


# Settings -------------------------------------------------------------------------------------------------------
def _secret(name: str) -> str:
    return decrypt(db.get_setting(f"secret:{name}", "") or "")


def slack_configured() -> bool:
    return bool(_secret("SLACK_BOT_TOKEN") and _secret("SLACK_APP_TOKEN"))


def slack_allowed() -> set[str]:
    return {x.strip().upper() for x in (db.get_setting("slack_allowed_users", "") or "").split(",") if x.strip()}


def discord_configured() -> bool:
    return bool(_secret("DISCORD_BOT_TOKEN"))


def discord_allowed() -> set[str]:
    return {x.strip() for x in (db.get_setting("discord_allowed_users", "") or "").split(",") if x.strip()}


def discord_invite_url() -> str:
    app_id = _discord.get("app_id") or db.get_setting("discord_app_id", "") or ""
    return (f"https://discord.com/oauth2/authorize?client_id={app_id}&scope=bot&permissions={DISCORD_PERMISSIONS}"
            if app_id else "")


def teammate_for_channel(channel: str) -> dict:
    raw = db.get_setting(f"{channel}_teammate_id", "") or ""
    try:
        return store.get(int(raw)) if raw else store.default()
    except HTTPException:
        return store.default()


def status() -> dict:
    from .. import telegram

    tg = telegram.status()
    return {
        "slack": {**_slack, "configured": slack_configured(), "allowed_users": db.get_setting("slack_allowed_users", "") or "",
                  "teammate": teammate_for_channel("slack")["id"], "steps": SLACK_STEPS, "manifest": SLACK_MANIFEST},
        "discord": {"running": _discord["running"], "bot": _discord["bot"], "error": _discord["error"],
                    "configured": discord_configured(), "allowed_users": db.get_setting("discord_allowed_users", "") or "",
                    "teammate": teammate_for_channel("discord")["id"], "steps": DISCORD_STEPS, "invite_url": discord_invite_url()},
        "telegram": {"configured": tg.get("configured"), "running": tg.get("running"), "bot": tg.get("bot"), "error": tg.get("error"),
                     "allowed_users": tg.get("allowed_users"), "teammate": teammate_for_channel("telegram")["id"], "steps": TELEGRAM_STEPS},
        **_more_status(),
    }


def _more_status() -> dict:
    from . import more_channels

    return more_channels.status()


def configure_slack(bot_token: str | None, app_token: str | None, allowed_users: str | None, teammate_id: int | None) -> dict:
    if bot_token is not None:
        if bot_token and not bot_token.startswith("xoxb-"):
            raise HTTPException(400, "The bot token starts with xoxb-")
        db.set_setting("secret:SLACK_BOT_TOKEN", encrypt(bot_token.strip()) if bot_token.strip() else "")
    if app_token is not None:
        if app_token and not app_token.startswith("xapp-"):
            raise HTTPException(400, "The app-level token starts with xapp-")
        db.set_setting("secret:SLACK_APP_TOKEN", encrypt(app_token.strip()) if app_token.strip() else "")
    if allowed_users is not None:
        db.set_setting("slack_allowed_users", ",".join(sorted({x.strip().upper() for x in allowed_users.split(",") if x.strip()})))
    if teammate_id is not None:
        store.get(teammate_id)
        db.set_setting("slack_teammate_id", str(teammate_id))
    return status()["slack"]


def configure_discord(bot_token: str | None, allowed_users: str | None, teammate_id: int | None) -> dict:
    if bot_token is not None:
        tok = bot_token.strip()
        if tok and (tok.count(".") != 2 or " " in tok):
            raise HTTPException(400, "That doesn't look like a Discord bot token (three parts separated by dots)")
        db.set_setting("secret:DISCORD_BOT_TOKEN", encrypt(tok) if tok else "")
        db.set_setting("discord_app_id", "")
        _discord.update(bot="", bot_id="", app_id="", error="")
    if allowed_users is not None:
        ids = {x.strip() for x in allowed_users.split(",") if x.strip()}
        if any(not x.isdigit() for x in ids):
            raise HTTPException(400, "Discord user IDs are numbers (turn on Developer Mode, then Copy User ID)")
        db.set_setting("discord_allowed_users", ",".join(sorted(ids)))
    if teammate_id is not None:
        store.get(teammate_id)
        db.set_setting("discord_teammate_id", str(teammate_id))
    return status()["discord"]


async def test_discord() -> dict:
    tok = _secret("DISCORD_BOT_TOKEN")
    if not tok:
        raise HTTPException(400, "Paste the bot token first")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{DISCORD}/users/@me", headers={"Authorization": f"Bot {tok}"})
        app = await c.get(f"{DISCORD}/oauth2/applications/@me", headers={"Authorization": f"Bot {tok}"})
    if r.status_code != 200:
        raise HTTPException(400, f"Discord said: {r.status_code} ({'token rejected' if r.status_code == 401 else 'error'})")
    me = r.json()
    if app.status_code == 200:
        db.set_setting("discord_app_id", str(app.json().get("id", "")))
    _discord.update(bot=me.get("username", ""), bot_id=str(me.get("id", "")), app_id=db.get_setting("discord_app_id", "") or "")
    return {"ok": True, "bot": me.get("username", ""), "invite_url": discord_invite_url()}


def configure_telegram(bot_token: str | None, allowed_users: str | None, teammate_id: int | None) -> dict:
    from .. import telegram

    if bot_token is not None and bot_token and ":" not in bot_token:
        raise HTTPException(400, "That doesn't look like a BotFather token (it has a colon in the middle)")
    telegram.configure(bot_token, allowed_users)
    if teammate_id is not None:
        store.get(teammate_id)
        db.set_setting("telegram_teammate_id", str(teammate_id))
    return status()["telegram"]


async def test_slack() -> dict:
    tok = _secret("SLACK_BOT_TOKEN")
    if not tok:
        raise HTTPException(400, "Paste the bot token first")
    async with httpx.AsyncClient(timeout=15) as c:
        r = (await c.post(f"{SLACK}/auth.test", headers={"Authorization": f"Bearer {tok}"})).json()
    if not r.get("ok"):
        raise HTTPException(400, f"Slack said: {r.get('error', 'not ok')}")
    return {"ok": True, "team": r.get("team", ""), "bot": r.get("user", "")}


# Conversations from a channel -------------------------------------------------------------------------------------
def on_channel_conversation(conversation_id: int, channel: str) -> None:
    """A new Telegram/Slack conversation belongs to the teammate chosen for that channel."""
    try:
        store.link(conversation_id, teammate_for_channel(channel)["id"])
    except Exception as e:  # noqa: BLE001 - never break the gateway
        log.warning("channel link skipped: %s", e)


def _slack_conversation(key: str) -> int:
    from .. import assistant

    c = db.one("SELECT id FROM conversations WHERE channel = 'slack' AND external_id = ? ORDER BY id DESC LIMIT 1", key)
    if c:
        return c["id"]
    cid = assistant.new_conversation("slack", "Slack chat", key)
    on_channel_conversation(cid, "slack")
    return cid


async def slack_api(method: str, **params) -> dict:
    tok = _secret("SLACK_BOT_TOKEN")
    async with httpx.AsyncClient(timeout=30) as c:
        r = (await c.post(f"{SLACK}/{method}", json=params, headers={"Authorization": f"Bearer {tok}"})).json()
    if not r.get("ok"):
        raise RuntimeError(r.get("error", f"Slack {method} failed"))
    return r


async def slack_post(channel: str, text: str, thread_ts: str | None = None) -> None:
    for i in range(0, len(text or " "), 3500):
        params = {"channel": channel, "text": text[i:i + 3500] or " "}
        if thread_ts:
            params["thread_ts"] = thread_ts
        await slack_api("chat.postMessage", **params)


async def handle_slack_event(ev: dict) -> str | None:
    """One Slack event -> one teammate turn, answered in the same DM or thread. Returns the reply (tests)."""
    from .. import actions, assistant

    if ev.get("bot_id") or ev.get("subtype") or ev.get("type") not in ("message", "app_mention"):
        return None
    if ev.get("type") == "message" and ev.get("channel_type") != "im":
        return None
    user, channel, text = (ev.get("user") or "").upper(), ev.get("channel", ""), (ev.get("text") or "").strip()
    thread = ev.get("thread_ts") or (ev.get("ts") if ev.get("type") == "app_mention" else None)
    if user not in slack_allowed():
        await slack_post(channel, f"This assistant is private. Your Slack member ID is {user}.", thread)
        return None
    if not text:
        return None
    key = f"slack:{channel}:{thread or ''}"
    req = f"slack:{channel}:{ev.get('ts', '')}"
    if ev.get("ts") and actions.by_key("turn:" + req):
        return None  # Slack retries; never run the same message twice
    ctx = assistant.Ctx(channel="slack", conversation_id=_slack_conversation(key), request_key=req if ev.get("ts") else "")
    reply, events = await assistant.ask(ctx, text)
    reply = _with_notes(reply, events, ctx.conversation_id)
    await slack_post(channel, reply or "(no reply)", thread)
    return reply


def _with_notes(reply: str, events: list[dict], conversation_id: int) -> str:
    """What a chat channel adds under the answer: actions held for approval, drafts waiting to be sent."""
    from . import outbox

    held = [e for e in events if e["type"] == "approval_required"]
    if held:
        reply += "\n\n" + "\n".join(f"Waiting for your OK in AI Workspace: {e['tool'].replace('_', ' ')} (#{e['id']})." for e in held)
    drafts = outbox.listing("draft", conversation_id)
    if drafts:
        reply += f"\n\n{len(drafts)} draft(s) are waiting in AI Workspace. Nothing is sent until you press Send there."
    return reply


async def slack_loop() -> None:
    import websockets

    while True:
        if not slack_configured():
            _slack.update(running=False, error="")
            await asyncio.sleep(10)
            continue
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = (await c.post(f"{SLACK}/apps.connections.open", headers={"Authorization": f"Bearer {_secret('SLACK_APP_TOKEN')}"})).json()
            if not r.get("ok"):
                raise RuntimeError(r.get("error", "apps.connections.open failed"))
            async with websockets.connect(r["url"], open_timeout=15, ping_interval=30) as ws:
                _slack.update(running=True, error="")
                async for raw in ws:
                    env = json.loads(raw)
                    if env.get("envelope_id"):
                        await ws.send(json.dumps({"envelope_id": env["envelope_id"]}))
                    if env.get("type") == "disconnect":
                        break
                    if env.get("type") == "events_api":
                        ev = (env.get("payload") or {}).get("event") or {}
                        try:
                            await handle_slack_event(ev)
                        except Exception as e:  # noqa: BLE001 - one bad message must not stop the bot
                            log.warning("slack message failed: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _slack.update(running=False, error=redact(str(e))[:300])
            await asyncio.sleep(15)


# Discord ----------------------------------------------------------------------------------------------------------
def _discord_conversation(key: str) -> int:
    from .. import assistant

    c = db.one("SELECT id FROM conversations WHERE channel = 'discord' AND external_id = ? ORDER BY id DESC LIMIT 1", key)
    if c:
        return c["id"]
    cid = assistant.new_conversation("discord", "Discord chat", key)
    on_channel_conversation(cid, "discord")
    return cid


async def discord_post(channel_id: str, text: str, reply_to: str | None = None) -> None:
    tok = _secret("DISCORD_BOT_TOKEN")
    async with httpx.AsyncClient(timeout=30) as c:
        for n, i in enumerate(range(0, len(text or " "), 1900)):
            body: dict = {"content": text[i:i + 1900] or " ", "allowed_mentions": {"parse": []}}  # never ping @everyone
            if reply_to and n == 0:
                body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
            r = await c.post(f"{DISCORD}/channels/{channel_id}/messages", json=body, headers={"Authorization": f"Bot {tok}"})
            if r.status_code >= 400:
                raise RuntimeError(f"Discord send failed: {r.status_code}")


async def handle_discord_message(m: dict, bot_id: str | None = None) -> str | None:
    """One Discord MESSAGE_CREATE -> one teammate turn: DMs, or server messages that @mention the bot. Returns the reply."""
    import re

    from .. import actions, assistant

    bot_id = bot_id or _discord.get("bot_id") or ""
    author = m.get("author") or {}
    if author.get("bot") or m.get("webhook_id") or m.get("type", 0) not in (0, 19):  # 19: a reply
        return None
    guild = m.get("guild_id")
    if guild and not any(str(u.get("id")) == bot_id for u in m.get("mentions") or []):
        return None  # in a server, only messages that mention the bot
    user, channel = str(author.get("id", "")), str(m.get("channel_id", ""))
    text = re.sub(rf"<@!?{re.escape(bot_id)}>", "", m.get("content") or "").strip() if bot_id else (m.get("content") or "").strip()
    if user not in discord_allowed():
        await discord_post(channel, f"This assistant is private. Your Discord user ID is {user}.", m.get("id") if guild else None)
        return None
    if not text:
        return None
    req = f"discord:{channel}:{m.get('id', '')}"
    if m.get("id") and actions.by_key("turn:" + req):
        return None  # a reconnect can replay a message; never run it twice
    ctx = assistant.Ctx(channel="discord", conversation_id=_discord_conversation(f"discord:{channel}"),
                        request_key=req if m.get("id") else "")
    reply, events = await assistant.ask(ctx, text)
    reply = _with_notes(reply, events, ctx.conversation_id)
    await discord_post(channel, reply or "(no reply)", m.get("id") if guild else None)
    return reply


async def _gateway_url(tok: str) -> str:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{DISCORD}/gateway/bot", headers={"Authorization": f"Bot {tok}"})
    if r.status_code != 200:
        raise RuntimeError("bot token rejected" if r.status_code == 401 else f"gateway lookup failed ({r.status_code})")
    return r.json()["url"] + "/?v=10&encoding=json"


async def discord_loop() -> None:
    import websockets

    while True:
        if not discord_configured():
            _discord.update(running=False, error="")
            await asyncio.sleep(10)
            continue
        beat: asyncio.Task | None = None
        try:
            tok = _secret("DISCORD_BOT_TOKEN")
            async with websockets.connect(await _gateway_url(tok), open_timeout=15, max_size=2 ** 22) as ws:
                seq: list = [None]
                hello = json.loads(await ws.recv())
                interval = hello["d"]["heartbeat_interval"] / 1000

                async def heartbeat() -> None:
                    while True:
                        await asyncio.sleep(interval)
                        await ws.send(json.dumps({"op": 1, "d": seq[0]}))

                beat = asyncio.create_task(heartbeat())
                await ws.send(json.dumps({"op": 2, "d": {"token": tok, "intents": DISCORD_INTENTS,
                                                         "properties": {"os": "linux", "browser": "ai-workspace", "device": "ai-workspace"}}}))
                async for raw in ws:
                    ev = json.loads(raw)
                    if ev.get("s") is not None:
                        seq[0] = ev["s"]
                    op, t = ev.get("op"), ev.get("t")
                    if op == 1:
                        await ws.send(json.dumps({"op": 1, "d": seq[0]}))
                    elif op in (7, 9):  # reconnect / invalid session: start a fresh session
                        break
                    elif t == "READY":
                        me = ev["d"]["user"]
                        app_id = str((ev["d"].get("application") or {}).get("id", ""))
                        if app_id:
                            db.set_setting("discord_app_id", app_id)
                        _discord.update(running=True, error="", bot=me.get("username", ""), bot_id=str(me["id"]), app_id=app_id)
                    elif t == "MESSAGE_CREATE":
                        try:
                            await handle_discord_message(ev["d"])
                        except Exception as e:  # noqa: BLE001 - one bad message must not stop the bot
                            log.warning("discord message failed: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _discord.update(running=False, error=redact(str(e))[:300])
            await asyncio.sleep(15)
            continue
        finally:
            if beat:
                beat.cancel()
        _discord.update(running=False)
        await asyncio.sleep(2)

