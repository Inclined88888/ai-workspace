"""Chat channels: talk to your teammates from Slack and Telegram (feat/grok-style).

Slack uses Socket Mode, so like the Telegram gateway it needs no public URL: the app opens an outgoing WebSocket to
Slack. Setup is a short wizard: create the app from our manifest (one paste), install it, paste the two tokens, pick which
teammate answers. Only the Slack users you list get answers.

Safety: a message that arrives on a channel is a request from you, answered in that same chat. It can never make a
teammate send anything to anyone else: outgoing messages are drafts (outbox) that you send from the app or phone, and
email/invites still need your approval every time.
WhatsApp: the WhatsApp Business Cloud API needs a verified Meta business, a public webhook and a paid number, so it is a
documented follow-up (docs/channels.md), not a one-click setup.
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
        "telegram": {"configured": tg.get("configured"), "running": tg.get("running"), "bot": tg.get("bot"), "error": tg.get("error"),
                     "allowed_users": tg.get("allowed_users"), "teammate": teammate_for_channel("telegram")["id"], "steps": TELEGRAM_STEPS},
        "whatsapp": {"available": False, "why": "WhatsApp needs a verified Meta business account and a public webhook. "
                     "See docs/channels.md for the steps; it isn't one-click yet."},
    }


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
    held = [e for e in events if e["type"] == "approval_required"]
    if held:
        reply += "\n\n" + "\n".join(f"Waiting for your OK in AI Workspace: {e['tool'].replace('_', ' ')} (#{e['id']})." for e in held)
    from . import outbox

    drafts = outbox.listing("draft", ctx.conversation_id)
    if drafts:
        reply += f"\n\n{len(drafts)} draft(s) are waiting in AI Workspace. Nothing is sent until you press Send there."
    await slack_post(channel, reply or "(no reply)", thread)
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
