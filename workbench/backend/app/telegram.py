"""Telegram gateway: chat with the same assistant (same memory and skills) from Telegram.

Long polling, so no public URL or open port is needed. Only allowlisted Telegram user ids get answers; everyone
else is told the bot is private. Scheduled tasks with deliver=telegram go to the home chat (your private chat).
"""

import asyncio
import logging

import httpx

from fastapi import HTTPException

from . import approvals, assistant, db
from .security import decrypt, encrypt

log = logging.getLogger("telegram")
API = "https://api.telegram.org"
_offset = 0
_status = {"running": False, "bot": "", "error": ""}


def token() -> str:
    return decrypt(db.get_setting("secret:TELEGRAM_BOT_TOKEN", "") or "")


def allowed() -> set[int]:
    raw = db.get_setting("telegram_allowed_users", "") or ""
    return {int(x) for x in raw.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}


def configure(bot_token: str | None, allowed_users: str | None) -> None:
    if bot_token is not None:
        db.set_setting("secret:TELEGRAM_BOT_TOKEN", encrypt(bot_token.strip()) if bot_token.strip() else "")
    if allowed_users is not None:
        ids = ",".join(str(i) for i in sorted({int(x) for x in allowed_users.replace(" ", "").split(",") if x.strip().isdigit()}))
        db.set_setting("telegram_allowed_users", ids)
        if ids:
            db.set_setting("telegram_home_chat", ids.split(",")[0])


def status() -> dict:
    return {**_status, "configured": bool(token()), "allowed_users": db.get_setting("telegram_allowed_users", "") or ""}


async def _api(client: httpx.AsyncClient, method: str, **params):
    r = await client.post(f"{API}/bot{token()}/{method}", json=params)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", f"Telegram {method} failed"))
    return data["result"]


async def send(client: httpx.AsyncClient, chat_id: int, text: str, markup: dict | None = None) -> None:
    text = text or "(empty reply)"
    parts = [text[i : i + 4000] for i in range(0, len(text), 4000)]  # Telegram caps messages at 4096 characters
    for n, part in enumerate(parts):
        extra = {"reply_markup": markup} if markup and n == len(parts) - 1 else {}
        await _api(client, "sendMessage", chat_id=chat_id, text=part, **extra)


async def _reply(client: httpx.AsyncClient, chat_id: int, text: str, events: list[dict]) -> None:
    """Send the answer; a paused tool call gets Approve and Decline buttons."""
    ask = next((e for e in events if e.get("type") == "approval"), None)
    if not ask:
        await send(client, chat_id, text)
        return
    body = f"{text}\n\nTool: {ask['name']}\n{ask['args'][:800]}"
    buttons = {"inline_keyboard": [[{"text": "Approve", "callback_data": f"ap:{ask['id']}:1"},
                                    {"text": "Decline", "callback_data": f"ap:{ask['id']}:0"}]]}
    await send(client, chat_id, body, buttons)


async def _decide(client: httpx.AsyncClient, cb: dict) -> None:
    user_id = (cb.get("from") or {}).get("id")
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if user_id not in allowed():
        await _api(client, "answerCallbackQuery", callback_query_id=cb["id"], text="This assistant is private.")
        return
    try:
        _, aid, yes = (cb.get("data") or "").split(":")
        a = approvals.claim(int(aid), yes == "1")
    except (ValueError, HTTPException) as e:
        await _api(client, "answerCallbackQuery", callback_query_id=cb["id"], text=getattr(e, "detail", "Not valid any more"))
        return
    await _api(client, "answerCallbackQuery", callback_query_id=cb["id"], text="Approved" if yes == "1" else "Declined")
    if chat_id and msg.get("message_id"):  # remove the buttons so the same choice cannot be sent twice
        await _api(client, "editMessageReplyMarkup", chat_id=chat_id, message_id=msg["message_id"], reply_markup={"inline_keyboard": []})
    events, final, error = [], "", ""
    async for e in assistant.resume(a, yes == "1"):
        events.append(e)
        if e["type"] == "message":
            final = e["content"]
        elif e["type"] == "error":
            error = e["error"]
    await _reply(client, chat_id, final or (f"Error: {error}" if error else ""), events)


def notify(text: str) -> None:
    """Fire and forget delivery to the home chat (used by schedules and the company)."""
    chat = db.get_setting("telegram_home_chat", "")
    if not (token() and chat):
        return

    async def go():
        async with httpx.AsyncClient(timeout=30) as c:
            await send(c, int(chat), text)

    try:
        asyncio.get_running_loop().create_task(go())
    except RuntimeError:
        pass


def _conversation(chat_id: int, fresh: bool = False) -> int:
    if not fresh:
        c = db.one("SELECT id FROM conversations WHERE channel = 'telegram' AND external_id = ? ORDER BY id DESC LIMIT 1", str(chat_id))
        if c:
            return c["id"]
    return assistant.new_conversation("telegram", "Telegram chat", str(chat_id))


async def _handle(client: httpx.AsyncClient, msg: dict) -> None:
    chat_id, user_id = msg["chat"]["id"], (msg.get("from") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if user_id not in allowed():
        await send(client, chat_id, f"This assistant is private. Your Telegram user id is {user_id}.")
        return
    if not text:
        await send(client, chat_id, "I can read text messages for now.")
        return
    if text in ("/start", "/help"):
        await send(client, chat_id, "Talk to me normally. /new starts a fresh conversation (memory and skills carry over).")
        return
    if text == "/new":
        _conversation(chat_id, fresh=True)
        await send(client, chat_id, "New conversation started.")
        return
    await _api(client, "sendChatAction", chat_id=chat_id, action="typing")
    ctx = assistant.Ctx(channel="telegram", conversation_id=_conversation(chat_id))
    reply, events = await assistant.ask(ctx, text)
    await _reply(client, chat_id, reply, events)


async def loop() -> None:
    global _offset
    while True:
        if not token():
            _status.update(running=False, bot="", error="")
            await asyncio.sleep(10)
            continue
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as client:
                me = await _api(client, "getMe")
                _status.update(running=True, bot=me.get("username", ""), error="")
                while token():
                    updates = await _api(client, "getUpdates", offset=_offset, timeout=30, allowed_updates=["message", "callback_query"])
                    for u in updates:
                        _offset = u["update_id"] + 1
                        try:
                            if u.get("message"):
                                await _handle(client, u["message"])
                            elif u.get("callback_query"):
                                await _decide(client, u["callback_query"])
                        except Exception as e:  # noqa: BLE001 - one bad message must not stop the bot
                            log.warning("telegram update failed: %s", e)
        except Exception as e:  # noqa: BLE001
            _status.update(running=False, error=str(e)[:300])
            await asyncio.sleep(15)
